"""Child process with fixed dispatch, inherited immutable input FD and bounded resources."""
import json
import os
import resource
import sys
import time

def execute(request):
    import numpy as np
    from threadpoolctl import threadpool_limits
    args = request['arguments']
    op = args['operation']
    fd = request['fd']
    with threadpool_limits(limits=request['threads']):
        if request['kind'] == 'tpch':
            import duckdb
            # The OS path is generated from an inherited integer FD, never model input.
            c = duckdb.connect(f'/proc/self/fd/{fd}', read_only=True)
            c.execute(f"SET threads={int(request['threads'])}")
            group, value = args.get('group_by'), args.get('value')
            if op == 'group_sum':
                if group not in ('l_returnflag', 'l_shipmode') or value not in ('l_quantity', 'l_extendedprice'):
                    raise ValueError('invalid grouping/measure combination')
                rows = c.execute(f'SELECT "{group}", sum("{value}") FROM lineitem GROUP BY 1 ORDER BY 1').fetchall()
                result = {'rows': [[a, float(b)] for a, b in rows]}
            elif op == 'filtered_count':
                if group not in ('l_shipmode', 'l_returnflag'):
                    raise ValueError('invalid filter field')
                value = c.execute(f'SELECT count(*) FROM lineitem WHERE "{group}"=?',
                                  [args['filter_value']]).fetchone()[0]
                result = {'value': int(value)}
            elif op == 'join_sum':
                if group != 'c_mktsegment' or value != 'o_totalprice':
                    raise ValueError('invalid join fields')
                rows = c.execute('SELECT c_mktsegment, sum(o_totalprice) FROM customer JOIN orders '
                                 'ON c_custkey=o_custkey GROUP BY 1 ORDER BY 1').fetchall()
                result = {'rows': [[a, float(b)] for a,b in rows]}
            elif op == 'window_rank':
                if group != 'o_orderpriority' or value != 'o_totalprice':
                    raise ValueError('invalid ranking fields')
                rows = c.execute('SELECT o_orderpriority, o_orderkey, o_totalprice FROM '
                                 '(SELECT *,row_number() OVER(PARTITION BY o_orderpriority '
                                 'ORDER BY o_totalprice DESC,o_orderkey) AS r FROM orders) '
                                 'WHERE r=1 ORDER BY o_orderpriority').fetchall()
                result = {'rows': [[a,int(b),float(c)] for a,b,c in rows]}
            else:
                raise ValueError('tabular operation required')
            c.close()
        elif request['kind'] == 'numpy':
            with os.fdopen(os.dup(fd), 'rb') as stream:
                with np.load(stream, allow_pickle=False) as data:
                    X, y = data['X'], data['y']
            from sklearn.decomposition import PCA
            from sklearn.cluster import KMeans
            from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier, HistGradientBoostingClassifier
            from sklearn.linear_model import Ridge,LogisticRegression
            from sklearn.model_selection import StratifiedKFold, KFold, cross_val_score, train_test_split
            from sklearn.svm import SVC
            if op == 'pca':
                model = PCA(n_components=args['components'], svd_solver='full').fit(X)
                result = {'value': float(model.explained_variance_ratio_.sum())}
            elif op == 'kmeans':
                model = KMeans(n_clusters=args['clusters'], n_init=10, random_state=42).fit(X)
                result = {'value': float(model.inertia_)}
            elif op in ('random_forest','logistic'):
                train_x,test_x,train_y,test_y = train_test_split(X,y,test_size=0.25,random_state=42,stratify=y)
                model = (RandomForestClassifier(n_estimators=args['trees'], random_state=42,n_jobs=1)
                         if op=='random_forest' else LogisticRegression(C=1.0,max_iter=args['iterations'],random_state=42))
                model.fit(train_x,train_y)
                result = {'value': float(model.score(test_x,test_y))}
            else:
                if op == 'ridge_cv':
                    model = Ridge(alpha=args['alpha'])
                    cv = KFold(n_splits=args['folds'], shuffle=True, random_state=42)
                    scoring = 'neg_mean_squared_error'
                else:
                    models = {
                        'forest_cv': lambda: RandomForestClassifier(n_estimators=args['trees'], max_depth=None,
                                                                    random_state=42, n_jobs=1),
                        'extra_trees_cv': lambda: ExtraTreesClassifier(n_estimators=args['trees'], random_state=42,n_jobs=1),
                        'histboost_cv': lambda: HistGradientBoostingClassifier(max_iter=args['iterations'], random_state=42),
                        'svc_cv': lambda: SVC(kernel=args['kernel'], C=1.0),
                    }
                    if op not in models:
                        raise ValueError('unsupported numerical operation')
                    model = models[op]()
                    cv = StratifiedKFold(n_splits=args['folds'],shuffle=True,random_state=42)
                    scoring = 'accuracy'
                scores = cross_val_score(model,X,y,cv=cv,scoring=scoring,n_jobs=1)
                result = {'value': float(scores.mean()), 'fold_scores': scores.tolist()}
        else:
            raise ValueError('unknown resource format')
    return result

def main():
    begin=time.monotonic_ns()
    request = json.load(sys.stdin)
    memory = int(request['memory_gib'])*1024**3
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (240, 240))
    result = execute(request)
    if os.environ.get('LEASEPATH_TOOL_TIMING_ENVELOPE')=='1':
        result={'result':result,'worker_execute_ns':time.monotonic_ns()-begin}
    print(json.dumps(result,allow_nan=False))

if __name__ == '__main__':
    main()
