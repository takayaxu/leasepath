"""Illustrative distributions only; no benchmark or fitted research results."""
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
out = Path(__file__).resolve().parent
plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False, 'pdf.fonttype': 42})
fig, axs = plt.subplots(1, 3, figsize=(12, 3.7), layout='constrained')
ax=axs[0]
ax.barh(['A: next step', 'B: next step'], [0.1, 10], color=['#087f8c','#d97732'])
ax.set_xscale('log');ax.set_xlim(.03,20);ax.set_xlabel('Next-use delay (seconds, log scale)');ax.set_title('(a) Equal hops, unequal time')
ax.text(.12,0,'0.1 s',va='center');ax.text(10.5,1,'10 s',va='center')
ax=axs[1];t=np.array([0,.1,1,5,9.9,11])
ax.step(t,[0,.5,.5,.5,1,1],where='post',label='A: 50% at 0.1 s, 50% at 9.9 s',color='#087f8c')
ax.step(t,[0,0,0,1,1,1],where='post',label='B: always 5 s',color='#d97732')
ax.axvline(1,color='#666',linestyle=':');ax.set_ylim(-.03,1.05);ax.set_xlabel('Retention horizon (seconds)');ax.set_ylabel('P(next use within horizon)');ax.set_title('(b) Same mean = 5 s');ax.legend(fontsize=7,loc='lower right')
ax=axs[2];a=np.linspace(0,10,401);w=np.array([.5,.5]);rates=np.array([10.,.1]);s=np.exp(-a[:,None]*rates);m=(s*w/rates).sum(1)/(s*w).sum(1)
ax.plot(a,m,color='#087f8c',label='Conditional mean residual life')
ax.plot(a,np.maximum(5.05-a,0),'--',color='#d97732',label='Initial mean minus elapsed')
ax.set_xlabel('Elapsed time (seconds)');ax.set_ylabel('Expected remaining seconds');ax.set_title('(c) Long-tail mixture');ax.legend(fontsize=7,loc='lower right')
fig.suptitle('Why next-use prediction needs time distributions',fontsize=14)
fig.savefig(out/'prediction_motivation.png',dpi=180)
fig.savefig(out/'prediction_motivation.pdf')
print('Saved illustrative figure. Panel (c): equal mixture of Exp(mean=0.1 s) and Exp(mean=10 s).')
