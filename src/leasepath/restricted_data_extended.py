"""Bounded, reusable numerical operations for the 38-parent extension; no task IDs or code input."""
import numpy as np


def integer(lo,hi,description):return {'type':'integer','minimum':lo,'maximum':hi,'description':description}
def number(lo,hi,description):return {'type':'number','minimum':lo,'maximum':hi,'description':description}
def enum(values,description):return {'enum':values,'description':description}
COL=integer(0,7,'Zero-based numerical column index in X.')
# Each branch is an independent reusable operation with required, bounded parameters.
PARAMETERS={
 'impute':{'strategy':enum(['mean','median'],'Fill missing values using the selected column statistic.')},
 'quantile':{'column':COL,'q':number(0.05,0.95,'Quantile probability; linear interpolation.')},
 'weighted_group_mean':{'group_column':COL,'value_column':COL,'weight_column':COL},
 'correlation':{'columns':{'type':'array','items':COL,'minItems':2,'maxItems':4,'uniqueItems':True}},
 'deduplicate':{'key_columns':{'type':'array','items':COL,'minItems':1,'maxItems':3,'uniqueItems':True},'value_column':COL},
 'histogram':{'column':COL,'bins':integer(2,20,'Number of equal-width bins over [-4,4].')},
 'chi_square':{'row_column':COL,'column_column':COL},
 'bootstrap_mean':{'column':COL,'resamples':integer(500,3000,'IID bootstrap resamples; RNG seed 42.'),'confidence':number(0.8,0.99,'Two-sided percentile confidence level.')},
 'permutation_mean':{'column':COL,'resamples':integer(500,3000,'Random label permutations; RNG seed 42; plus-one p value.')},
 'paired_t':{'first_column':COL,'second_column':COL},
 'anova':{'group_column':COL,'value_column':COL},
 'mann_whitney':{'first_column':COL,'second_column':COL},
 'linear_trend':{'column':COL},
 'iqr_outliers':{'column':COL,'factor':number(1,3,'Tukey IQR fence multiplier.')},
 'asof_align':{'direction':enum(['backward','forward'],'Match each X[:,0] left time against sorted right timestamps y; X[:,1] is right value.')},
 'rolling_mean':{'column':COL,'window':integer(3,101,'Trailing sample count; valid windows only.')},
 'time_resample':{'period':integer(10,3600,'Time bucket width; X[:,0] time, X[:,1] value.')},
 'sessionize':{'gap':integer(5,120,'Start a new session when consecutive sorted times in X[:,0] differ by more than gap.')},
 'interpolate':{'method':enum(['linear','nearest'],'Interpolate X[:,1] at query times y, using finite observations with times X[:,0].')},
 'spectrum':{'column':COL,'sample_rate':number(10,1000,'Samples per second; exclude DC when finding the dominant frequency.')},
 'cross_correlation':{'max_lag':integer(10,200,'Find lag maximizing correlation of X[:,0] with X[:,1], restricted to this absolute lag.')},
 'lowpass':{'column':COL,'cutoff':number(1,40,'Butterworth low-pass cutoff Hz.'),'sample_rate':number(90,1000,'Samples per second.'),'order':integer(2,6,'Filter order; zero-phase filtering.')},
 'find_peaks':{'column':COL,'distance':integer(5,200,'Minimum peak spacing in samples.'),'prominence':number(0.1,2,'Minimum peak prominence.')},
 'integrate':{'method':enum(['trapezoid','simpson'],'Integrate X[:,1] against X[:,0].')},
 'polynomial_root':{'lower':number(-10,10,'Lower bracket.'),'upper':number(-10,10,'Upper bracket; polynomial coefficients are X[:,0] in descending order.')},
 'simplex_optimize':{'total':number(0.5,2,'Required sum of weights.'),'upper_bound':number(0.5,2,'Upper bound per weight; minimize 0.5*w.T*X*w+y.T*w.')},
 'banded_solve':{'diagonal_shift':number(0,2,'Add to main diagonal; X columns are lower, main and upper diagonals, y is RHS.')},
 'nmf':{'components':integer(2,8,'Nonnegative factors.'),'iterations':integer(100,300,'Maximum coordinate-descent iterations, random initialization seed 42.')},
 'ica':{'components':integer(2,6,'Independent components; unit variance whitening.'),'iterations':integer(100,500,'Maximum FastICA iterations; seed 42.')},
 'dbscan':{'eps':number(0.1,2,'Euclidean neighborhood radius.'),'min_samples':integer(3,30,'Minimum samples per dense neighborhood.')},
 'neighbors':{'k':integer(2,10,'Neighbors per query.'),'queries':integer(10,1000,'First this many X rows are query points; rest are training points.')},
 'isolation':{'trees':integer(50,300,'Isolation forest estimators; seed 42.'),'contamination':number(0.01,0.2,'Expected outlier fraction.')},
 'mutual_information':{'neighbors':integer(3,10,'KNN information estimator, classification target y, seed 42.')},
 'autoregression':{'lags':integer(2,12,'OLS autoregression lag count, with intercept.'),'horizon':integer(2,8,'Recursive forecast horizon.')},
 'kaplan_meier':{'horizon':number(0.5,10,'Survival probability at this time; X[:,0] durations, X[:,1] event indicators.')},
 'connected_components':{'connectivity':enum([1,2],'2D pixel connectivity: 1=4-neighbor, 2=8-neighbor.'),'min_size':integer(2,50,'Minimum region size counted.')},
 'assignment':{'maximize':{'type':'boolean','description':'Choose maximum rather than minimum total assignment cost.'}},
 'wasserstein':{'order':enum([1,2],'Equal-size empirical distribution Wasserstein order; samples are X[:,0] and X[:,1].')},
}
DESCRIPTIONS={
 'impute':'Column-wise missing value imputation: report fill values, missing counts and imputed column means.',
 'quantile':'Estimate a selected scalar quantile of one column.',
 'weighted_group_mean':'Weighted value means for sorted categorical groups.',
 'correlation':'Pearson correlation matrix for selected columns.',
 'deduplicate':'Keep the first row for each composite key; report retained count and retained value sum.',
 'histogram':'Count samples in equal-width bins between -4 and 4, numpy histogram endpoint conventions.',
 'chi_square':'Test independence of two categorical columns, no continuity correction.',
 'bootstrap_mean':'Percentile bootstrap confidence interval for a column mean.',
 'permutation_mean':'Two-sided permutation test of group means; binary labels are y.',
 'paired_t':'Two-sided paired-sample t test, first minus second.',
 'anova':'One-way ANOVA across integer group labels.',
 'mann_whitney':'Two-sided asymptotic Mann-Whitney U, continuity correction enabled.',
 'linear_trend':'OLS regression of y against selected X column: slope, intercept and R squared.',
 'iqr_outliers':'Count and fraction outside Tukey IQR fences.',
 'asof_align':'Temporal as-of join: count matches, sum matched values, first 5 matched values.',
 'rolling_mean':'Summarize valid trailing rolling means: count, mean, min and max.',
 'time_resample':'Nonnegative integer time buckets: count buckets and first 5 (bucket time, sum) rows.',
 'sessionize':'Split sorted event timestamps by inactivity: session count, mean events and maximum duration.',
 'interpolate':'Interpolate missing observations: query count, first 5 values and mean over queries.',
 'spectrum':'Dominant positive FFT frequency and its fraction of non-DC power.',
 'cross_correlation':'Find best signed lag between two signal columns; center signals before correlation.',
 'lowpass':'Zero-phase Butterworth low-pass: filtered mean and RMS.',
 'find_peaks':'Detect signal peaks: count, first 5 sample indices, mean peak height.',
 'integrate':'Definite numerical integration of sampled data.',
 'polynomial_root':'Find one polynomial root in a sign-changing bracket; return root and residual.',
 'simplex_optimize':'Bounded convex quadratic portfolio allocation: optimal objective and total weight.',
 'banded_solve':'Solve a diagonally dominant tridiagonal system; report solution mean, norm and maximum residual.',
 'nmf':'Nonnegative matrix factorization: Frobenius reconstruction error.',
 'ica':'FastICA source separation: mean absolute excess kurtosis of recovered signals.',
 'dbscan':'Density clustering: cluster count, noise count and largest cluster size.',
 'neighbors':'Exact neighbor retrieval: mean kth distance and first 5 nearest training row indices.',
 'isolation':'Isolation forest anomaly detection: outlier count and mean decision score.',
 'mutual_information':'Feature relevance: per-column mutual information and top 3 feature indices, ties by index.',
 'autoregression':'Fit a lagged linear autoregression to X[:,0], forecast recursively.',
 'kaplan_meier':'Censored survival analysis: survival at horizon and median event time.',
 'connected_components':'Count connected foreground image regions of at least min_size; largest size.',
 'assignment':'One-to-one square cost matrix assignment: total cost and number assigned.',
 'wasserstein':'Compare two empirical one-dimensional distributions by transport distance.',
}

def execute_extended(op,X,y,a):
    from scipy import stats,signal,integrate,optimize,sparse
    if op not in PARAMETERS:raise ValueError('unsupported extended operation')
    for key in ('column','group_column','value_column','weight_column','row_column','column_column','first_column','second_column'):
        if key in a and not 0<=a[key]<X.shape[1]:raise ValueError('column outside dataset')
    for key in ('columns','key_columns'):
        if key in a and max(a[key])>=X.shape[1]:raise ValueError('column outside dataset')
    if op=='impute':
        fill=np.nanmean(X,axis=0) if a['strategy']=='mean' else np.nanmedian(X,axis=0)
        clean=np.where(np.isnan(X),fill,X)
        return {'fill_values':fill.tolist(),'missing_counts':np.isnan(X).sum(axis=0).tolist(),'means':clean.mean(axis=0).tolist()}
    if op=='quantile':return {'value':float(np.quantile(X[:,a['column']],a['q'],method='linear'))}
    if op=='weighted_group_mean':
        g,v,w=X[:,a['group_column']],X[:,a['value_column']],X[:,a['weight_column']]
        if np.any(w<=0):raise ValueError('positive weights required')
        return {'rows':[[int(k),float(np.sum(v[g==k]*w[g==k])/w[g==k].sum())] for k in np.unique(g)]}
    if op=='correlation':return {'matrix':np.corrcoef(X[:,a['columns']],rowvar=False).tolist()}
    if op=='deduplicate':
        _,idx=np.unique(X[:,a['key_columns']],axis=0,return_index=True)
        return {'count':int(len(idx)),'value_sum':float(X[idx,a['value_column']].sum())}
    if op=='histogram':return {'counts':np.histogram(X[:,a['column']],bins=a['bins'],range=(-4,4))[0].tolist()}
    if op=='chi_square':
        _,r=np.unique(X[:,a['row_column']],return_inverse=True);_,c=np.unique(X[:,a['column_column']],return_inverse=True)
        tab=np.zeros((r.max()+1,c.max()+1),dtype=int);np.add.at(tab,(r,c),1)
        value,p,dof,_=stats.chi2_contingency(tab,correction=False)
        return {'statistic':float(value),'pvalue':float(p),'dof':int(dof)}
    if op=='bootstrap_mean':
        v=X[:,a['column']];rng=np.random.default_rng(42)
        means=np.array([v[rng.integers(0,len(v),size=len(v))].mean() for _ in range(a['resamples'])])
        tail=(1-a['confidence'])/2
        return {'mean':float(v.mean()),'lower':float(np.quantile(means,tail)),'upper':float(np.quantile(means,1-tail))}
    if op=='permutation_mean':
        v=X[:,a['column']];mask=y==0;observed=v[mask].mean()-v[~mask].mean();rng=np.random.default_rng(42);exceed=0
        for _ in range(a['resamples']):
            labels=rng.permutation(y);exceed+=abs(v[labels==0].mean()-v[labels!=0].mean())>=abs(observed)
        return {'difference':float(observed),'pvalue':float((exceed+1)/(a['resamples']+1))}
    if op=='paired_t':
        t,p=stats.ttest_rel(X[:,a['first_column']],X[:,a['second_column']])
        return {'statistic':float(t),'pvalue':float(p)}
    if op=='anova':
        g,v=X[:,a['group_column']],X[:,a['value_column']];f,p=stats.f_oneway(*(v[g==k] for k in np.unique(g)))
        return {'statistic':float(f),'pvalue':float(p)}
    if op=='mann_whitney':
        u,p=stats.mannwhitneyu(X[:,a['first_column']],X[:,a['second_column']],alternative='two-sided',method='asymptotic',use_continuity=True)
        return {'statistic':float(u),'pvalue':float(p)}
    if op=='linear_trend':
        fit=stats.linregress(X[:,a['column']],y)
        return {'slope':float(fit.slope),'intercept':float(fit.intercept),'r_squared':float(fit.rvalue**2)}
    if op=='iqr_outliers':
        v=X[:,a['column']];q1,q3=np.quantile(v,[.25,.75]);margin=a['factor']*(q3-q1);n=int(((v<q1-margin)|(v>q3+margin)).sum())
        return {'count':n,'fraction':n/len(v)}
    if op=='asof_align':
        idx=np.searchsorted(y,X[:,0],side='right' if a['direction']=='backward' else 'left')-(a['direction']=='backward')
        valid=(idx>=0)&(idx<len(y));v=X[idx[valid],1]
        return {'matched':int(valid.sum()),'value_sum':float(v.sum()),'first_values':v[:5].tolist()}
    if op=='rolling_mean':
        v=X[:,a['column']];w=a['window'];s=np.r_[0,np.cumsum(v)];r=(s[w:]-s[:-w])/w
        return {'count':len(r),'mean':float(r.mean()),'min':float(r.min()),'max':float(r.max())}
    if op=='time_resample':
        bucket=(X[:,0]//a['period']).astype(int);keys=np.unique(bucket);sums=np.bincount(bucket,weights=X[:,1])
        return {'buckets':len(keys),'rows':[[int(k*a['period']),float(sums[k])] for k in keys[:5]]}
    if op=='sessionize':
        t=X[:,0];starts=np.r_[0,np.flatnonzero(np.diff(t)>a['gap'])+1];ends=np.r_[starts[1:]-1,len(t)-1]
        return {'sessions':len(starts),'mean_events':float(len(t)/len(starts)),'max_duration':float(np.max(t[ends]-t[starts]))}
    if op=='interpolate':
        from scipy.interpolate import interp1d
        mask=np.isfinite(X[:,1]);r=interp1d(X[mask,0],X[mask,1],kind=a['method'])(y)
        return {'count':len(r),'first_values':r[:5].tolist(),'mean':float(r.mean())}
    if op=='spectrum':
        v=X[:,a['column']];power=np.abs(np.fft.rfft(v-v.mean()))**2;power[0]=0;k=np.argmax(power)
        return {'frequency':float(k*a['sample_rate']/len(v)),'power_fraction':float(power[k]/power.sum())}
    if op=='cross_correlation':
        left=X[:,0]-X[:,0].mean();right=X[:,1]-X[:,1].mean();r=signal.correlate(left,right,mode='full',method='fft');lags=signal.correlation_lags(len(left),len(right));m=abs(lags)<=a['max_lag']
        return {'lag':int(lags[m][np.argmax(r[m])])}
    if op=='lowpass':
        if a['cutoff']>=a['sample_rate']/2:raise ValueError('cutoff must be below Nyquist')
        sos=signal.butter(a['order'],a['cutoff'],fs=a['sample_rate'],output='sos');r=signal.sosfiltfilt(sos,X[:,a['column']])
        return {'mean':float(r.mean()),'rms':float(np.sqrt(np.mean(r*r)))}
    if op=='find_peaks':
        v=X[:,a['column']];idx,_=signal.find_peaks(v,distance=a['distance'],prominence=a['prominence'])
        if not len(idx):raise ValueError('no qualifying peaks')
        return {'count':len(idx),'first_indices':idx[:5].tolist(),'mean_height':float(v[idx].mean())}
    if op=='integrate':
        fn=integrate.trapezoid if a['method']=='trapezoid' else integrate.simpson
        return {'value':float(fn(X[:,1],x=X[:,0]))}
    if op=='polynomial_root':
        if a['lower']>=a['upper']:raise ValueError('invalid bracket')
        r=optimize.brentq(lambda x:np.polyval(X[:,0],x),a['lower'],a['upper'],xtol=1e-14)
        return {'root':float(r),'residual':float(np.polyval(X[:,0],r))}
    if op=='simplex_optimize':
        q=X;fit=optimize.minimize(lambda w:float(.5*w@q@w+y@w),np.full(len(y),a['total']/len(y)),jac=lambda w:q@w+y,method='SLSQP',bounds=[(0,a['upper_bound'])]*len(y),constraints=[{'type':'eq','fun':lambda w:w.sum()-a['total'],'jac':lambda w:np.ones_like(w)}],options={'ftol':1e-14,'maxiter':300})
        if not fit.success:raise ValueError('optimizer did not converge')
        return {'objective':float(fit.fun),'weight_sum':float(fit.x.sum())}
    if op=='banded_solve':
        from scipy.sparse.linalg import spsolve
        q=sparse.diags([X[1:,0],X[:,1]+a['diagonal_shift'],X[:-1,2]],[-1,0,1],format='csc');z=spsolve(q,y)
        return {'mean':float(z.mean()),'norm':float(np.linalg.norm(z)),'residual':float(abs(q@z-y).max())}
    if op=='nmf':
        from sklearn.decomposition import NMF
        m=NMF(n_components=a['components'],init='random',random_state=42,max_iter=a['iterations'],tol=1e-4);w=m.fit_transform(X)
        return {'error':float(np.linalg.norm(X-w@m.components_))}
    if op=='ica':
        from sklearn.decomposition import FastICA
        z=FastICA(n_components=a['components'],max_iter=a['iterations'],random_state=42,whiten='unit-variance',tol=1e-4).fit_transform(X)
        return {'mean_absolute_kurtosis':float(abs(stats.kurtosis(z,axis=0,fisher=True,bias=True)).mean())}
    if op=='dbscan':
        from sklearn.cluster import DBSCAN
        labels=DBSCAN(eps=a['eps'],min_samples=a['min_samples'],algorithm='kd_tree',n_jobs=1).fit(X).labels_;valid=labels[labels>=0];counts=np.bincount(valid)
        return {'clusters':len(counts),'noise':int((labels<0).sum()),'largest':int(counts.max(initial=0))}
    if op=='neighbors':
        from sklearn.neighbors import NearestNeighbors
        n=a['queries'];m=NearestNeighbors(n_neighbors=a['k'],algorithm='brute',n_jobs=1).fit(X[n:]);dist,idx=m.kneighbors(X[:n])
        return {'mean_kth_distance':float(dist[:,-1].mean()),'first_indices':idx[:5,0].tolist()}
    if op=='isolation':
        from sklearn.ensemble import IsolationForest
        m=IsolationForest(n_estimators=a['trees'],contamination=a['contamination'],random_state=42,n_jobs=1).fit(X);scores=m.decision_function(X)
        return {'outliers':int((scores<0).sum()),'mean_score':float(scores.mean())}
    if op=='mutual_information':
        from sklearn.feature_selection import mutual_info_classif
        values=mutual_info_classif(X,y,n_neighbors=a['neighbors'],random_state=42,n_jobs=1)
        return {'values':values.tolist(),'top_indices':np.argsort(-values,kind='stable')[:3].tolist()}
    if op=='autoregression':
        v=X[:,0];p=a['lags'];z=np.column_stack([np.ones(len(v)-p)]+[v[p-i-1:len(v)-i-1] for i in range(p)]);coef=np.linalg.lstsq(z,v[p:],rcond=None)[0];history=v.tolist();forecast=[]
        for _ in range(a['horizon']):
            value=float(coef[0]+np.dot(coef[1:],history[-p:][::-1]));history.append(value);forecast.append(value)
        return {'forecast':forecast}
    if op=='kaplan_meier':
        t,e=X[:,0],X[:,1];times,inv,counts=np.unique(t,return_inverse=True,return_counts=True);deaths=np.bincount(inv,weights=e);risk=len(t)-np.r_[0,np.cumsum(counts)[:-1]];surv=np.cumprod(1-deaths/risk);idx=np.searchsorted(times,a['horizon'],side='right')-1;med=np.flatnonzero(surv<=.5)
        if not len(med):raise ValueError('median not reached')
        return {'survival':float(surv[idx]) if idx>=0 else 1.0,'median':float(times[med[0]])}
    if op=='connected_components':
        from scipy.ndimage import label,generate_binary_structure
        labels,n=label(X!=0,structure=generate_binary_structure(2,a['connectivity']));counts=np.bincount(labels.ravel())[1:];counts=counts[counts>=a['min_size']]
        return {'regions':len(counts),'largest':int(counts.max(initial=0))}
    if op=='assignment':
        rows,cols=optimize.linear_sum_assignment(X,maximize=a['maximize'])
        return {'cost':float(X[rows,cols].sum()),'assigned':len(rows)}
    if op=='wasserstein':
        value=stats.wasserstein_distance(X[:,0],X[:,1]) if a['order']==1 else np.sqrt(np.mean((np.sort(X[:,0])-np.sort(X[:,1]))**2))
        return {'value':float(value)}
    raise AssertionError('missing fixed operation')
