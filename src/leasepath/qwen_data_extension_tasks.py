"""38 original analysis intents; fixed inputs and independently implemented reference computations."""
from pathlib import Path
import json
import numpy as np
from .restricted_data_tools import file_hash


def definitions():
    # Group keys describe analysis intent, never seed/scale/wording variants.
    rows=[
      ('missing_median','impute',{'strategy':'median'},'Fill missing values by each column median. Report fill values, missing counts and resulting column means.'),
      ('upper_quantile','quantile',{'column':1,'q':.9},'Find the 90th percentile of column 1 using linear interpolation.'),
      ('weighted_groups','weighted_group_mean',{'group_column':0,'value_column':1,'weight_column':2},'Compute value column 1 weighted by column 2 within each integer group in column 0; sort groups.'),
      ('feature_correlation','correlation',{'columns':[0,1,2]},'Compute the Pearson correlation matrix of columns 0, 1 and 2.'),
      ('composite_dedup','deduplicate',{'key_columns':[0,1],'value_column':2},'Deduplicate on columns 0 and 1, keeping the first row. Report retained row count and sum of column 2 on retained rows.'),
      ('distribution_bins','histogram',{'column':0,'bins':8},'Count column 0 values in 8 equal-width bins over [-4,4], using numpy histogram endpoint conventions.'),
      ('categorical_independence','chi_square',{'row_column':0,'column_column':1},'Test independence of categorical columns 0 and 1 using Pearson chi-square without continuity correction. Return statistic, pvalue and degrees of freedom.'),
      ('bootstrap_interval','bootstrap_mean',{'column':0,'resamples':1000,'confidence':.95},'Estimate a 95% percentile bootstrap confidence interval for the mean of column 0 using 1000 resamples and RNG seed 42. Return sample mean and interval endpoints.'),
      ('randomization_test','permutation_mean',{'column':0,'resamples':1000},'Test mean difference of column 0 between y=0 and y=1 with 1000 random label permutations, seed 42. Use two-sided absolute differences and plus-one pvalue; report difference group 0 minus 1.'),
      ('paired_measurements','paired_t',{'first_column':0,'second_column':1},'Perform a two-sided paired t test of column 0 minus column 1; report statistic and pvalue.'),
      ('multi_group_anova','anova',{'group_column':0,'value_column':1},'Perform one-way ANOVA of column 1 across categorical groups in column 0; report statistic and pvalue.'),
      ('rank_sum_test','mann_whitney',{'first_column':0,'second_column':1},'Perform a two-sided asymptotic Mann-Whitney U test comparing columns 0 and 1, with continuity correction; report statistic and pvalue.'),
      ('response_trend','linear_trend',{'column':0},'Fit unregularized OLS of y on column 0 with intercept. Report slope, intercept and R squared.'),
      ('robust_fences','iqr_outliers',{'column':0,'factor':1.5},'Count column 0 observations outside Tukey fences Q1-1.5*IQR and Q3+1.5*IQR; report count and fraction.'),
      ('temporal_asof','asof_align',{'direction':'backward'},'For each sorted left timestamp X[:,0], match the latest right timestamp y no later than it. Right values are X[:,1]. Report matched count, matched value sum and first 5 matched values.'),
      ('window_smoothing','rolling_mean',{'column':0,'window':51},'Compute trailing 51-sample rolling means of column 0, keeping valid windows only. Report count, mean, minimum and maximum of these means.'),
      ('time_buckets','time_resample',{'period':60},'Aggregate values X[:,1] into 60-second buckets floor(X[:,0]/60)*60. Report number of occupied buckets and first 5 sorted bucket sums.'),
      ('activity_sessions','sessionize',{'gap':30},'Sessionize sorted events X[:,0]: a gap greater than 30 starts a new session. Return session count, mean events per session and maximum last-minus-first duration.'),
      ('gap_interpolation','interpolate',{'method':'linear'},'Use linear interpolation of finite values X[:,1] at times X[:,0] to evaluate query times y. Report query count, first 5 values and mean over all queries.'),
      ('dominant_frequency','spectrum',{'column':0,'sample_rate':128},'For column 0 sampled at 128 Hz, find the dominant positive FFT frequency after centering, and its fraction of all non-DC power.'),
      ('signal_alignment','cross_correlation',{'max_lag':100},'Find signed lag maximizing full cross-correlation of centered X[:,0] with centered X[:,1], restricting absolute lag to 100.'),
      ('noise_filtering','lowpass',{'column':0,'cutoff':10,'sample_rate':100,'order':4},'Apply order-4 zero-phase Butterworth low-pass to column 0 with 10 Hz cutoff at 100 Hz sample rate. Report filtered mean and RMS.'),
      ('event_peaks','find_peaks',{'column':0,'distance':50,'prominence':.8},'Detect peaks in column 0 with minimum distance 50 samples and prominence 0.8. Return count, first 5 indices and mean peak height.'),
      ('curve_area','integrate',{'method':'trapezoid'},'Integrate X[:,1] against nonuniform sample positions X[:,0] using the trapezoid rule.'),
      ('bracketed_root','polynomial_root',{'lower':0,'upper':2},'Find the real polynomial root in [0,2]. Descending coefficients are X[:,0]. Return root and polynomial residual.'),
      ('portfolio_allocation','simplex_optimize',{'total':1,'upper_bound':1},'Minimize 0.5*w.T*X*w+y.T*w subject to sum(w)=1 and each weight in [0,1]. Report optimal objective and weight sum.'),
      ('tridiagonal_system','banded_solve',{'diagonal_shift':.5},'Solve a tridiagonal system: X columns 0/1/2 are lower/main/upper diagonals, y is RHS. Add 0.5 to the main diagonal. Return solution mean, Euclidean norm and max absolute residual.'),
      ('nonnegative_factors','nmf',{'components':6,'iterations':200},'Fit rank-6 NMF to nonnegative X, random initialization seed 42, coordinate descent, tol=1e-4, max_iter=200. Report Frobenius reconstruction error.'),
      ('independent_sources','ica',{'components':5,'iterations':300},'Separate 5 independent components using FastICA, unit-variance whitening, seed 42, tol=1e-4, max_iter=300. Report mean absolute excess kurtosis (biased estimator) across recovered signals.'),
      ('density_clusters','dbscan',{'eps':.65,'min_samples':8},'Apply Euclidean DBSCAN with eps=0.65 and min_samples=8. Return non-noise cluster count, noise count and largest cluster size.'),
      ('neighbor_retrieval','neighbors',{'k':5,'queries':1000},'Use the first 1000 X rows as queries and remaining rows as training points. Find exact 5 nearest Euclidean neighbors. Report mean fifth distance and nearest training-relative index for the first 5 queries.'),
      ('anomaly_detection','isolation',{'trees':100,'contamination':.05},'Fit IsolationForest with 100 estimators, contamination=0.05, seed 42, single job. Return number with negative decision score and mean decision score.'),
      ('feature_information','mutual_information',{'neighbors':3},'Estimate classification mutual information of every X column against y using 3 neighbors, seed 42. Return values and top 3 feature indices in descending order, ties by column index.'),
      ('recursive_forecast','autoregression',{'lags':8,'horizon':5},'Fit OLS AR(8) with intercept to X[:,0], then recursively forecast the next 5 values. Return forecast array.'),
      ('censored_survival','kaplan_meier',{'horizon':3},'Estimate Kaplan-Meier survival from X[:,0] durations and X[:,1] event indicators. Report survival at time 3 and first event time when survival reaches 0.5 or below.'),
      ('image_regions','connected_components',{'connectivity':1,'min_size':8},'Treat X as a binary image. Label 4-neighbor connected foreground regions, retain those with at least 8 pixels, and report region count and largest size.'),
      ('cost_assignment','assignment',{'maximize':False},'Find the minimum-cost one-to-one assignment of rows to columns in square cost matrix X. Return total cost and assigned count.'),
      ('distribution_transport','wasserstein',{'order':1},'Compute the order-1 Wasserstein distance between equally weighted empirical samples in columns 0 and 1.'),
    ]
    assert len(rows)==38
    return [dict(id=name,source_group='extension_'+name,operation=op,parameters=params,intent=intent,kind='numpy',planned_band='medium' if op in ['nmf','ica','dbscan','neighbors','isolation','mutual_information'] else 'short') for name,op,params,intent in rows]


def make_input(op,rng):
    # Necessary data for the intent; no sleeps, artificial repetitions or model-dependent sizing.
    X=rng.normal(size=(20000,3));y=np.zeros(len(X))
    if op=='impute':
        X=rng.normal(size=(50000,4));X[rng.random(X.shape)<.08]=np.nan
    elif op=='quantile':X=rng.standard_t(5,size=(100000,3))
    elif op=='weighted_group_mean':X=np.column_stack([rng.integers(0,4,100000),rng.normal(size=100000),rng.uniform(.1,3,100000)])
    elif op=='correlation':X[:,1]=.7*X[:,0]+.5*X[:,1]
    elif op=='deduplicate':X=np.column_stack([rng.integers(0,300,100000),rng.integers(0,100,100000),rng.normal(size=100000)])
    elif op=='histogram':X=rng.normal(size=(300000,2))
    elif op=='chi_square':X=np.column_stack([rng.integers(0,4,10000),rng.integers(0,3,10000)])
    elif op=='bootstrap_mean':X=rng.lognormal(0,.6,size=(4000,2))
    elif op=='permutation_mean':
        X=rng.normal(size=(1000,2));y=np.repeat([0,1],500);X[:,0]+=.06*y
    elif op=='paired_t':X=rng.normal(size=(4000,2));X[:,1]=X[:,0]+rng.normal(0,.5,4000)+.005
    elif op=='anova':X=np.column_stack([rng.integers(0,4,10000),rng.normal(size=10000)]);X[:,1]+=.01*X[:,0]
    elif op=='mann_whitney':X=rng.normal(size=(2000,2));X[:,1]+=.02
    elif op=='linear_trend':y=1.7*X[:,0]+.4+rng.normal(size=len(X))
    elif op=='iqr_outliers':X=rng.standard_t(4,size=(200000,2))
    elif op=='asof_align':X=np.column_stack([np.sort(rng.uniform(0,1000,20000)),rng.normal(size=20000)]);y=np.sort(rng.uniform(0,1000,len(X)))
    elif op=='rolling_mean':X=rng.normal(size=(200000,2))
    elif op=='time_resample':X=np.column_stack([np.sort(rng.integers(0,86400,100000)),rng.uniform(1,5,100000)])
    elif op=='sessionize':X=np.cumsum(rng.exponential(12,size=100000))[:,None]
    elif op=='interpolate':
        times=np.linspace(0,20,10000);v=np.sin(times)+.05*rng.normal(size=len(times));v[rng.random(len(v))<.2]=np.nan;v[0]=0;v[-1]=np.sin(20);X=np.column_stack([times,v]);y=np.linspace(.01,19.99,101)
    elif op=='spectrum':
        t=np.arange(65536)/128;X=(np.sin(2*np.pi*20*t)+.3*np.sin(2*np.pi*7*t)+.1*rng.normal(size=len(t)))[:,None]
    elif op=='cross_correlation':
        v=rng.normal(size=20000);X=np.column_stack([v,np.roll(v,37)+.1*rng.normal(size=len(v))])
    elif op=='lowpass':
        t=np.arange(200000)/100;X=(np.sin(2*np.pi*3*t)+.5*np.sin(2*np.pi*25*t)+.1*rng.normal(size=len(t)))[:,None]
    elif op=='find_peaks':
        t=np.arange(50000);X=(np.sin(2*np.pi*t/120)+.05*rng.normal(size=len(t)))[:,None]
    elif op=='integrate':
        t=np.r_[0,np.cumsum(rng.uniform(.0001,.001,100000))];X=np.column_stack([t,np.exp(-t/20)*np.sin(t)+2])
    elif op=='polynomial_root':X=np.array([[1.],[0.],[-2.]]);y=np.zeros(3)
    elif op=='simplex_optimize':X=np.array([[4.,1.,.2],[1.,2.,.1],[.2,.1,1.]]);y=np.array([.1,.05,.08])
    elif op=='banded_solve':X=np.column_stack([rng.uniform(-.3,.3,100000),np.full(100000,2.),rng.uniform(-.3,.3,100000)]);y=rng.normal(size=len(X))
    elif op=='nmf':X=rng.uniform(size=(10000,6))@rng.uniform(size=(6,80))+.01*rng.uniform(size=(10000,80))
    elif op=='ica':
        z=np.column_stack([rng.laplace(size=30000),rng.uniform(-1,1,30000),rng.standard_t(5,30000),rng.exponential(size=30000),rng.choice([-1,1],30000)+.05*rng.normal(size=30000)]);X=z@rng.normal(size=(5,8))
    elif op=='dbscan':
        from sklearn.datasets import make_blobs
        X,_=make_blobs(n_samples=12000,n_features=4,centers=5,cluster_std=.35,random_state=42)
    elif op=='neighbors':X=rng.normal(size=(50000,20))
    elif op=='isolation':X=rng.normal(size=(100000,15));X[:3000]+=rng.uniform(4,8,(3000,15))
    elif op=='mutual_information':
        from sklearn.datasets import make_classification
        X,y=make_classification(n_samples=20000,n_features=8,n_informative=4,n_redundant=1,random_state=42,shuffle=False)
    elif op=='autoregression':
        from scipy.signal import lfilter
        X=lfilter([1],[1,-.6,.15],rng.normal(size=100000))[:,None]
    elif op=='kaplan_meier':
        event=rng.exponential(3,100000);censor=rng.exponential(8,100000);X=np.column_stack([np.minimum(event,censor),(event<=censor).astype(float)])
    elif op=='connected_components':
        X=np.zeros((256,256))
        for _ in range(120):
            r,c=rng.integers(0,240,2);h,w=rng.integers(2,15,2);X[r:r+h,c:c+w]=1
    elif op=='assignment':X=rng.uniform(0,100,size=(64,64))
    elif op=='wasserstein':X=np.column_stack([rng.normal(size=100000),rng.normal(.5,1.4,100000)])
    if op not in ['permutation_mean','linear_trend','asof_align','interpolate','simplex_optimize','banded_solve','mutual_information']:y=np.zeros(len(X))
    return X,y


def prepare(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False);(root/'inputs').mkdir()
    tasks=definitions()
    for i,t in enumerate(tasks):
        X,y=make_input(t['operation'],np.random.default_rng(51000+i))
        path=root/'inputs'/(t['id']+'.npz');np.savez(path,X=X,y=y)
        t.update(file=str(path.resolve()),dataset_id='d'+t['id'],input_sha256=file_hash(path))
        op=t['operation']
        alternative='quantile' if op not in ['quantile','connected_components','assignment','simplex_optimize'] else 'histogram'
        t['operations']=[op,alternative]
        sample=X[:3,:min(6,X.shape[1])].astype(float)
        t['metadata']={'shape':list(X.shape),'dtype':str(X.dtype),'columns':['X','y'],'sample':{'X':[[float(v) if np.isfinite(v) else None for v in row] for row in sample],'y':y[:3].tolist()},'sampling_rule':'first 3 rows and first 6 columns; missing shown as null','resource_semantics':t['intent']}
        t['primary_step']='compute:'+op
        t['source']={'url':'https://docs.scipy.org/doc/scipy/' if op not in ['nmf','ica','dbscan','neighbors','isolation','mutual_information'] else 'https://scikit-learn.org/1.7/', 'implementation_version':'numpy-2.5.4/scipy-1.18.1/scikit-learn-1.7.2','license':'Original task wording and synthetic inputs; NumPy/SciPy/sklearn BSD; no external benchmark score','adaptation':'Original bounded parameter analysis intent, generated data; no DS-1000 task code copied'}
        t['split']='development'
        t['independence_review']={'unit':'analysis intent','variant_of_initial_twelve':False,'note':'Shared library/source families retained in provenance; not an independent-source holdout.'}
    (root/'tasks.private.json').write_text(json.dumps(tasks,indent=2,allow_nan=False)+'\n')
    (root/'tasks.public.json').write_text(json.dumps([{k:v for k,v in t.items() if k not in ['parameters','file','input_sha256']} for t in tasks],indent=2)+'\n')
    return tasks


def reference(task):
    """Separate formulas/algorithms where practical; never calls restricted dispatch."""
    from scipy import stats,signal,integrate,optimize,linalg,sparse
    with np.load(task['file'],allow_pickle=False) as data:X,y=data['X'],data['y']
    a=task['parameters'];op=task['operation'];col=a.get('column',0)
    if op=='impute':
        fills=[];counts=[];means=[]
        for v in X.T:
            finite=v[~np.isnan(v)];fill=float(np.median(finite) if a['strategy']=='median' else finite.mean());n=int(np.isnan(v).sum());fills.append(fill);counts.append(n);means.append(float((finite.sum()+n*fill)/len(v)))
        return {'fill_values':fills,'missing_counts':counts,'means':means}
    if op=='quantile':
        v=np.sort(X[:,col]);p=(len(v)-1)*a['q'];lo=int(p);return {'value':float(v[lo]+(p-lo)*(v[min(lo+1,len(v)-1)]-v[lo]))}
    if op=='weighted_group_mean':
        import duckdb
        c=duckdb.connect();c.register('data',{'g':X[:,a['group_column']],'v':X[:,a['value_column']],'w':X[:,a['weight_column']]});rows=c.execute('SELECT g,SUM(v*w)/SUM(w) FROM data GROUP BY g ORDER BY g').fetchall();c.close()
        return {'rows':[[int(k),float(v)] for k,v in rows]}
    if op=='correlation':
        z=X[:,a['columns']];z=z-z.mean(axis=0);norm=np.sqrt((z*z).sum(axis=0));return {'matrix':((z.T@z)/np.outer(norm,norm)).tolist()}
    if op=='deduplicate':
        seen=set();indices=[]
        for i,row in enumerate(X):
            key=tuple(row[a['key_columns']])
            if key not in seen:seen.add(key);indices.append(i)
        return {'count':len(indices),'value_sum':float(X[indices,a['value_column']].sum())}
    if op=='histogram':
        v=X[:,col];edges=np.linspace(-4,4,a['bins']+1);return {'counts':[int(((v>=lo)&((v<hi) if i<a['bins']-1 else (v<=hi))).sum()) for i,(lo,hi) in enumerate(zip(edges,edges[1:]))]}
    if op=='chi_square':
        groups=np.unique(X[:,a['row_column']]);cols=np.unique(X[:,a['column_column']]);tab=np.array([[np.count_nonzero((X[:,a['row_column']]==g)&(X[:,a['column_column']]==c)) for c in cols] for g in groups]);expected=np.outer(tab.sum(axis=1),tab.sum(axis=0))/tab.sum();value=float(((tab-expected)**2/expected).sum());dof=(len(groups)-1)*(len(cols)-1)
        return {'statistic':value,'pvalue':float(stats.chi2.sf(value,dof)),'dof':dof}
    if op=='bootstrap_mean':
        v=X[:,col];rng=np.random.default_rng(42);idx=rng.integers(0,len(v),size=(a['resamples'],len(v)));m=v[idx].mean(axis=1);tail=(1-a['confidence'])/2;bounds=np.percentile(m,[100*tail,100*(1-tail)])
        return {'mean':float(v.mean()),'lower':float(bounds[0]),'upper':float(bounds[1])}
    if op=='permutation_mean':
        v=X[:,col];observed=float(np.mean(v[y==0])-np.mean(v[y==1]));rng=np.random.default_rng(42);null=[]
        for _ in range(a['resamples']):
            order=rng.permutation(len(y));label=y[order];null.append(np.mean(v[label==0])-np.mean(v[label==1]))
        return {'difference':observed,'pvalue':float((1+sum(abs(s)>=abs(observed) for s in null))/(a['resamples']+1))}
    if op=='paired_t':
        delta=X[:,a['first_column']]-X[:,a['second_column']];stat=delta.mean()/(delta.std(ddof=1)/np.sqrt(len(delta)))
        return {'statistic':float(stat),'pvalue':float(2*stats.t.sf(abs(stat),len(delta)-1))}
    if op=='anova':
        g,v=X[:,a['group_column']],X[:,a['value_column']];parts=[v[g==k] for k in np.unique(g)];between=sum(len(p)*(p.mean()-v.mean())**2 for p in parts);within=sum(((p-p.mean())**2).sum() for p in parts);d1=len(parts)-1;d2=len(v)-len(parts);f=between/d1/(within/d2)
        return {'statistic':float(f),'pvalue':float(stats.f.sf(f,d1,d2))}
    if op=='mann_whitney':
        left=X[:,a['first_column']];right=X[:,a['second_column']];n=len(left);m=len(right);ranks=stats.rankdata(np.r_[left,right]);u=float(ranks[:n].sum()-n*(n+1)/2);z=(abs(u-n*m/2)-.5)/np.sqrt(n*m*(n+m+1)/12)
        return {'statistic':u,'pvalue':float(2*stats.norm.sf(z))}
    if op=='linear_trend':
        v=X[:,col];design=np.c_[np.ones(len(v)),v];coef=np.linalg.lstsq(design,y,rcond=None)[0];err=y-design@coef
        return {'slope':float(coef[1]),'intercept':float(coef[0]),'r_squared':float(1-np.dot(err,err)/np.sum((y-y.mean())**2))}
    if op=='iqr_outliers':
        v=X[:,col];q=stats.scoreatpercentile(v,[25,75]);lo=q[0]-a['factor']*(q[1]-q[0]);hi=q[1]+a['factor']*(q[1]-q[0]);n=len(v)-np.count_nonzero((v>=lo)&(v<=hi))
        return {'count':int(n),'fraction':float(n/len(v))}
    if op=='asof_align':
        import bisect
        vals=[]
        for t in X[:,0]:
            i=bisect.bisect_right(y,t)-1 if a['direction']=='backward' else bisect.bisect_left(y,t)
            if 0<=i<len(y):vals.append(float(X[i,1]))
        return {'matched':len(vals),'value_sum':float(np.sum(vals)),'first_values':vals[:5]}
    if op=='rolling_mean':
        r=np.convolve(X[:,col],np.ones(a['window'])/a['window'],mode='valid')
        return {'count':len(r),'mean':float(r.mean()),'min':float(r.min()),'max':float(r.max())}
    if op=='time_resample':
        buckets={}
        for t,v in X:b=int(t//a['period'])*a['period'];buckets[b]=buckets.get(b,0.)+v
        return {'buckets':len(buckets),'rows':[[k,float(buckets[k])] for k in sorted(buckets)[:5]]}
    if op=='sessionize':
        segments=np.split(X[:,0],np.flatnonzero(np.diff(X[:,0])>a['gap'])+1)
        return {'sessions':len(segments),'mean_events':float(np.mean([len(s) for s in segments])),'max_duration':float(max(s[-1]-s[0] for s in segments))}
    if op=='interpolate':
        m=np.isfinite(X[:,1]);assert a['method']=='linear';values=np.interp(y,X[m,0],X[m,1])
        return {'count':len(values),'first_values':values[:5].tolist(),'mean':float(values.mean())}
    if op=='spectrum':
        from scipy.fft import rfft,rfftfreq
        v=X[:,col];p=abs(rfft(v-v.mean()))**2;p[0]=0;idx=int(np.argmax(p));return {'frequency':float(rfftfreq(len(v),1/a['sample_rate'])[idx]),'power_fraction':float(p[idx]/sum(p))}
    if op=='cross_correlation':
        u=X[:,0]-X[:,0].mean();v=X[:,1]-X[:,1].mean();lags=np.arange(-a['max_lag'],a['max_lag']+1);values=[np.dot(u[max(0,k):min(len(u),len(u)+k)],v[max(0,-k):min(len(v),len(v)-k)]) for k in lags]
        return {'lag':int(lags[np.argmax(values)])}
    if op=='lowpass':
        b,c=signal.butter(a['order'],a['cutoff']/(a['sample_rate']/2));v=signal.filtfilt(b,c,X[:,col]);return {'mean':float(v.mean()),'rms':float(np.sqrt(np.dot(v,v)/len(v)))}
    if op=='find_peaks':
        # Direct independent library call; fixed peak semantics include prominence/distance suppression.
        indices=signal.find_peaks(X[:,col],prominence=a['prominence'],distance=a['distance'])[0]
        return {'count':int(indices.size),'first_indices':indices[:5].tolist(),'mean_height':float(np.mean(X[indices,col]))}
    if op=='integrate':
        assert a['method']=='trapezoid';v=float(np.sum(np.diff(X[:,0])*(X[1:,1]+X[:-1,1])*.5));return {'value':v}
    if op=='polynomial_root':
        roots=np.roots(X[:,0]);r=next(float(v.real) for v in roots if abs(v.imag)<1e-12 and a['lower']<=v.real<=a['upper']);return {'root':r,'residual':float(sum(c*r**(len(X)-i-1) for i,c in enumerate(X[:,0])))}
    if op=='simplex_optimize':
        # This frozen positive-definite instance has an interior optimum; solve KKT directly.
        n=len(y);kkt=np.block([[X,np.ones((n,1))],[np.ones((1,n)),np.zeros((1,1))]]);w=np.linalg.solve(kkt,np.r_[-y,a['total']])[:n];assert np.all((w>0)&(w<a['upper_bound']))
        return {'objective':float(.5*w@X@w+y@w),'weight_sum':float(w.sum())}
    if op=='banded_solve':
        band=np.zeros((3,len(y)));band[0,1:]=X[:-1,2];band[1]=X[:,1]+a['diagonal_shift'];band[2,:-1]=X[1:,0];z=linalg.solve_banded((1,1),band,y);res=(X[:,1]+a['diagonal_shift'])*z-y;res[1:]+=X[1:,0]*z[:-1];res[:-1]+=X[:-1,2]*z[1:]
        return {'mean':float(z.mean()),'norm':float(np.sqrt(np.sum(z*z))),'residual':float(max(abs(res)))}
    if op=='nmf':
        from sklearn.decomposition import NMF
        m=NMF(n_components=a['components'],init='random',random_state=42,max_iter=a['iterations'],tol=1e-4).fit(X);return {'error':float(m.reconstruction_err_)}
    if op=='ica':
        from sklearn.decomposition import FastICA
        m=FastICA(n_components=a['components'],max_iter=a['iterations'],random_state=42,whiten='unit-variance',tol=1e-4).fit(X);z=m.transform(X);z=z-z.mean(axis=0);variance=(z*z).mean(axis=0);return {'mean_absolute_kurtosis':float(abs((z**4).mean(axis=0)/variance**2-3).mean())}
    if op=='dbscan':
        from sklearn.cluster import DBSCAN
        labels=DBSCAN(eps=a['eps'],min_samples=a['min_samples'],algorithm='kd_tree',n_jobs=1).fit_predict(X);groups=[np.count_nonzero(labels==v) for v in np.unique(labels) if v>=0]
        return {'clusters':len(groups),'noise':int(np.count_nonzero(labels==-1)),'largest':int(max(groups,default=0))}
    if op=='neighbors':
        from scipy.spatial import cKDTree
        n=a['queries'];distance,idx=cKDTree(X[n:]).query(X[:n],k=a['k'],workers=1)
        return {'mean_kth_distance':float(np.mean(distance[:,-1])),'first_indices':idx[:5,0].tolist()}
    if op=='isolation':
        from sklearn.ensemble import IsolationForest
        m=IsolationForest(n_estimators=a['trees'],contamination=a['contamination'],random_state=42,n_jobs=1);pred=m.fit_predict(X);score=m.score_samples(X)-m.offset_
        return {'outliers':int(np.count_nonzero(pred==-1)),'mean_score':float(np.mean(score))}
    if op=='mutual_information':
        from sklearn.feature_selection import mutual_info_classif
        values=mutual_info_classif(X,y,n_neighbors=a['neighbors'],random_state=42,n_jobs=1)
        return {'values':values.tolist(),'top_indices':sorted(range(len(values)),key=lambda k:(-values[k],k))[:3]}
    if op=='autoregression':
        v=X[:,0];p=a['lags'];windows=np.lib.stride_tricks.sliding_window_view(v,p+1);design=np.c_[np.ones(len(windows)),windows[:,:p][:,::-1]];beta=np.linalg.solve(design.T@design,design.T@windows[:,-1]);tail=list(v[-p:]);out=[]
        for _ in range(a['horizon']):new=float(np.dot(beta,np.r_[1,tail[::-1]]));out.append(new);tail=tail[1:]+[new]
        return {'forecast':out}
    if op=='kaplan_meier':
        order=np.argsort(X[:,0]);t=X[order,0];e=X[order,1];risk=len(t);surv=1.;at=1.;median=None
        for begin,end in zip(np.r_[0,np.flatnonzero(np.diff(t)!=0)+1],np.r_[np.flatnonzero(np.diff(t)!=0)+1,len(t)]):
            surv*=1-e[begin:end].sum()/risk
            if t[begin]<=a['horizon']:at=surv
            if median is None and surv<=.5:median=float(t[begin])
            risk-=end-begin
        return {'survival':float(at),'median':median}
    if op=='connected_components':
        from scipy.sparse.csgraph import connected_components
        coords=np.argwhere(X!=0);index={tuple(c):i for i,c in enumerate(coords)};rows=[];cols=[]
        for i,(r,c) in enumerate(coords):
            for dr,dc in [(1,0),(0,1)]+([(1,1),(1,-1)] if a['connectivity']==2 else []):
                j=index.get((r+dr,c+dc))
                if j is not None:rows.append(i);cols.append(j)
        graph=sparse.coo_matrix((np.ones(len(rows)),(rows,cols)),shape=(len(coords),len(coords))).tocsr();_,labels=connected_components(graph,directed=False);sizes=np.bincount(labels);sizes=sizes[sizes>=a['min_size']]
        return {'regions':int(len(sizes)),'largest':int(max(sizes,default=0))}
    if op=='assignment':
        n=len(X);constraints=sparse.vstack([sparse.kron(sparse.eye(n),np.ones((1,n))),sparse.kron(np.ones((1,n)),sparse.eye(n))],format='csr');fit=optimize.linprog((-X if a['maximize'] else X).ravel(),A_eq=constraints,b_eq=np.ones(2*n),bounds=(0,None),method='highs');assert fit.success and np.max(abs(fit.x-np.rint(fit.x)))<1e-8
        return {'cost':float(-fit.fun if a['maximize'] else fit.fun),'assigned':n}
    if op=='wasserstein':
        diff=np.sort(X[:,0])-np.sort(X[:,1]);return {'value':float(np.mean(abs(diff)) if a['order']==1 else np.sqrt(np.mean(diff**2)))}
    raise ValueError('no independent reference for '+op)
