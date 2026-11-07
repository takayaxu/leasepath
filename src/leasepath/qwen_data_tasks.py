"""Twelve adapted development parents; reference/checker data never enter tool schemas."""
import hashlib
import json
from pathlib import Path
import numpy as np

from .restricted_data_tools import file_hash

def definitions():
    # Planned bands are hypotheses. Classification is frozen from reference timing before model requests.
    return [
      dict(id='ship_groups', source_group='tpch_shipping_aggregation', planned_band='short', kind='tpch',
           intent='Sum lineitem quantities separately for each return flag. Return rows sorted by return flag.',
           operation='group_sum', parameters={'group_by':'l_returnflag','value':'l_quantity'}),
      dict(id='air_count', source_group='tpch_transport_filter', planned_band='short', kind='tpch',
           intent='Count lineitems whose ship mode is AIR.',
           operation='filtered_count', parameters={'group_by':'l_shipmode','filter_value':'AIR'}),
      dict(id='segment_orders', source_group='tpch_customer_join', planned_band='short', kind='tpch',
           intent='Join customers to orders and sum order total price for each customer market segment. Sort by segment.',
           operation='join_sum', parameters={'group_by':'c_mktsegment','value':'o_totalprice'}),
      dict(id='priority_top', source_group='tpch_partitioned_rank', planned_band='short', kind='tpch',
           intent='For each order priority, find its most expensive order. Break price ties by smallest order key. '
                  'Return priority, order key, price, sorted by priority.',
           operation='window_rank', parameters={'group_by':'o_orderpriority','value':'o_totalprice'}),
      dict(id='variance_pca',source_group='sklearn_latent_variance',planned_band='medium',kind='numpy',
           intent='Find the sum of explained variance ratios of the first 5 principal components, using full SVD.',
           operation='pca',parameters={'components':5},generator='low_rank',rows=200000,features=100),
      dict(id='cluster_inertia',source_group='sklearn_blob_clustering',planned_band='medium',kind='numpy',
           intent='Cluster the observations into 8 groups using KMeans with random_state=42 and n_init=10. Report inertia.',
           operation='kmeans',parameters={'clusters':8},generator='blobs',rows=150000,features=40),
      dict(id='forest_holdout',source_group='sklearn_noisy_holdout',planned_band='medium',kind='numpy',
           intent='Use a random forest of 100 trees (random_state=42) to classify the data. '
                  'Use the fixed stratified 25% test split, random_state=42. Report test accuracy.',
           operation='random_forest',parameters={'trees':100},generator='classification',rows=12000,features=24),
      dict(id='ridge_validation',source_group='sklearn_linear_regularization',planned_band='medium',kind='numpy',
           intent='Estimate ridge regression generalization with alpha=1.0, five shuffled KFold splits, random_state=42. '
                  'Report mean negative mean squared error and all fold scores.',
           operation='ridge_cv',parameters={'folds':5,'alpha':1.0},generator='regression',rows=150000,features=100),
      dict(id='forest_cv',source_group='sklearn_nonlinear_forest_validation',planned_band='long',kind='numpy',
           intent='Estimate a 200-tree random forest classifier with five shuffled stratified folds, random_state=42. '
                  'Report mean accuracy and all fold accuracies.',
           operation='forest_cv',parameters={'folds':5,'trees':200},generator='classification',rows=22000,features=40),
      dict(id='boosting_cv',source_group='sklearn_histogram_boosting',planned_band='long',kind='numpy',
           intent='Estimate histogram gradient boosting classification with max_iter=200 and five shuffled stratified folds, '
                  'random_state=42. Report mean accuracy and fold accuracies.',
           operation='histboost_cv',parameters={'folds':5,'iterations':200},generator='classification',rows=100000,features=40),
      dict(id='kernel_cv',source_group='sklearn_nonlinear_margin',planned_band='long',kind='numpy',
           intent='Estimate RBF support-vector classification (C=1) with five shuffled stratified folds, random_state=42. '
                  'Report mean accuracy and fold accuracies.',
           operation='svc_cv',parameters={'folds':5,'kernel':'rbf'},generator='classification',rows=20000,features=40),
      dict(id='extra_trees_cv',source_group='sklearn_randomized_tree_validation',planned_band='long',kind='numpy',
           intent='Estimate an extra-trees classifier with 300 trees and five shuffled stratified folds, random_state=42. '
                  'Report mean accuracy and fold accuracies.',
           operation='extra_trees_cv',parameters={'folds':5,'trees':300},generator='classification',rows=50000,features=50),
    ]

def prepare(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    inputs = root/'inputs'
    inputs.mkdir()
    import duckdb
    c = duckdb.connect(str(inputs/'tpch.duckdb'))
    c.execute("LOAD tpch")
    c.execute("CALL dbgen(sf=0.1)")
    c.execute("CHECKPOINT")
    c.close()
    from sklearn.datasets import make_classification,make_regression,make_blobs,make_low_rank_matrix
    tasks = definitions()
    for index, task in enumerate(tasks):
        if task['kind'] == 'numpy':
            n,d,seed=task['rows'],task['features'],2401+index
            if task['generator']=='classification':
                X,y=make_classification(n_samples=n,n_features=d,n_informative=d//2,
                                         n_redundant=d//4,class_sep=0.8,flip_y=0.02,random_state=seed)
            elif task['generator']=='regression':
                X,y=make_regression(n_samples=n,n_features=d,n_informative=d//2,noise=3,random_state=seed)
            elif task['generator']=='blobs':
                X,y=make_blobs(n_samples=n,n_features=d,centers=8,cluster_std=4,random_state=seed)
            else:
                X=make_low_rank_matrix(n_samples=n,n_features=d,effective_rank=10,random_state=seed)
                y=np.zeros(n)
            path=inputs/(task['id']+'.npz')
            np.savez(path,X=X,y=y)
            task['metadata']={'shape':list(X.shape),'dtype':str(X.dtype),'columns':['X','y'],
                              'sample':{'X':X[:3,:6].tolist(),'y':y[:3].tolist()},
                              'sampling_rule':'first 3 rows, first 6 numerical columns'}
        else:
            path=inputs/'tpch.duckdb'
            task['metadata']={'dataset':'TPC-H dbgen sf=0.1','tables':{
                'lineitem':['l_returnflag','l_linestatus','l_quantity','l_extendedprice','l_discount','l_shipmode'],
                'orders':['o_orderkey','o_custkey','o_orderpriority','o_totalprice'],
                'customer':['c_custkey','c_mktsegment']},'sampling_rule':'schema only'}
        task['file']=str(path.resolve())
        task['input_sha256']=file_hash(path)
        task['dataset_id']='d'+task['id']
        task['operations']=([task['operation'],'kmeans'] if task['operation']=='pca' else
                            [task['operation'],'pca'] if task['kind']=='numpy' else
                            ['group_sum','filtered_count','join_sum','window_rank'])
        task['source']={'url': 'https://duckdb.org/docs/current/core_extensions/tpch' if task['kind']=='tpch'
                        else 'https://scikit-learn.org/1.7/datasets/sample_generators.html',
                        'implementation_version': 'duckdb-1.5.3' if task['kind']=='tpch' else 'scikit-learn-1.7.2',
                        'license': 'DuckDB MIT; generated TPC-H derivative, no TPC benchmark claim'
                                   if task['kind']=='tpch' else 'scikit-learn BSD-3-Clause, generated data',
                        'adaptation': 'bounded parameter tool, generated inputs and original request; not original benchmark score'}
        task['primary_step']='compute:'+task['operation']
        # The evaluator/reference and allowed answer are controller-only.
    (root/'tasks.private.json').write_text(json.dumps(tasks,indent=2)+'\n')
    public=[{k:v for k,v in t.items() if k not in ('parameters','file','input_sha256')} for t in tasks]
    (root/'tasks.public.json').write_text(json.dumps(public,indent=2)+'\n')
    return tasks

def reference(task):
    """Separate evaluator: direct SQL/sklearn calls, never dispatch through model tool."""
    if task['kind']=='tpch':
        import duckdb
        c=duckdb.connect(task['file'],read_only=True)
        c.execute('SET threads=2')
        queries={
          'ship_groups':'SELECT l_returnflag,sum(l_quantity) FROM lineitem GROUP BY l_returnflag ORDER BY l_returnflag',
          'air_count':"SELECT count(*) FROM lineitem WHERE l_shipmode='AIR'",
          'segment_orders':'SELECT c.c_mktsegment,sum(o.o_totalprice) FROM orders o,customer c '
                           'WHERE o.o_custkey=c.c_custkey GROUP BY c.c_mktsegment ORDER BY c.c_mktsegment',
          'priority_top':'SELECT o_orderpriority,o_orderkey,o_totalprice FROM orders o WHERE o_orderkey='
                         '(SELECT i.o_orderkey FROM orders i WHERE i.o_orderpriority=o.o_orderpriority '
                         'ORDER BY i.o_totalprice DESC,i.o_orderkey LIMIT 1) ORDER BY o_orderpriority'}
        rows=c.execute(queries[task['id']]).fetchall()
        c.close()
        if task['id']=='air_count':
            return {'value':int(rows[0][0])}
        return {'rows':[[float(x) if not isinstance(x,(str,int)) else x for x in row] for row in rows]}
    with np.load(task['file'],allow_pickle=False) as data:
        X,y=data['X'],data['y']
    p=task['parameters']
    from sklearn.decomposition import PCA
    from sklearn.cluster import KMeans
    from sklearn.ensemble import RandomForestClassifier,HistGradientBoostingClassifier,ExtraTreesClassifier
    from sklearn.linear_model import Ridge
    from sklearn.svm import SVC
    from sklearn.model_selection import KFold,StratifiedKFold,train_test_split
    if task['id']=='variance_pca':
        model=PCA(n_components=5,svd_solver='full').fit(X)
        return {'value':float(sum(model.explained_variance_ratio_))}
    if task['id']=='cluster_inertia':
        model=KMeans(n_clusters=8,n_init=10,random_state=42).fit(X)
        return {'value':float(model.inertia_)}
    if task['id']=='forest_holdout':
        a,b,c,d=train_test_split(X,y,test_size=0.25,stratify=y,random_state=42)
        model=RandomForestClassifier(n_estimators=100,random_state=42,n_jobs=1).fit(a,c)
        return {'value':float(model.score(b,d))}
    if task['id']=='ridge_validation':
        splitter=KFold(n_splits=5,shuffle=True,random_state=42)
        make=lambda: Ridge(alpha=1.0)
    else:
        splitter=StratifiedKFold(n_splits=5,shuffle=True,random_state=42)
        make={
            'forest_cv':lambda:RandomForestClassifier(n_estimators=200,random_state=42,n_jobs=1),
            'boosting_cv':lambda:HistGradientBoostingClassifier(max_iter=200,random_state=42),
            'kernel_cv':lambda:SVC(kernel='rbf',C=1.0),
            'extra_trees_cv':lambda:ExtraTreesClassifier(n_estimators=300,random_state=42,n_jobs=1)
        }[task['id']]
    scores=[]
    for a,b in splitter.split(X,y):
        m=make().fit(X[a],y[a])
        if task['id']=='ridge_validation':
            score=-float(np.square(m.predict(X[b])-y[b]).mean())
        else:
            score=float(np.mean(m.predict(X[b])==y[b]))
        scores.append(score)
    return {'value':float(np.mean(scores)),'fold_scores':scores}

def equal(a,b):
    if isinstance(a,dict) or isinstance(b,dict):
        return isinstance(a,dict) and isinstance(b,dict) and a.keys()==b.keys() and all(equal(a[k],b[k]) for k in a)
    if isinstance(a,list) or isinstance(b,list):
        return isinstance(a,list) and isinstance(b,list) and len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
    if isinstance(a,(float,int)) and not isinstance(a,bool) and isinstance(b,(float,int)) and not isinstance(b,bool):
        return bool(np.isfinite(a) and np.isfinite(b) and np.isclose(a,b,rtol=1e-7,atol=1e-8))
    return type(a) is type(b) and a==b

def actual_band(seconds):
    return 'short' if seconds<3 else 'medium' if seconds<30 else 'long' if seconds<=180 else 'overlong'
