/* Storage verification only: bound CPU1800s, own affinity/NUMA/THP. Numerical tools retain frozen v1 launcher. */
#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <linux/mempolicy.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

static void die(const char *s) { perror(s); exit(125); }
static unsigned long integer(const char *s, unsigned long max) {
    char *end; errno=0; unsigned long v=strtoul(s,&end,10);
    if(errno || !*s || *end || *s=='-' || v>max) { errno=EINVAL; die("invalid numeric argument"); }
    return v;
}
int main(int argc,char **argv) {
    if(argc<7 || strcmp(argv[4],"--")!=0) {
        fprintf(stderr,"usage: launcher CPU,CPU NODE MEMORY_GIB -- /absolute/python -m module\n"); return 125;
    }
    cpu_set_t wanted,actual; CPU_ZERO(&wanted);
    char *list=strdup(argv[1]); if(!list) die("strdup");
    unsigned count=0; char *save=NULL;
    for(char *part=strtok_r(list,",",&save); part; part=strtok_r(NULL,",",&save)) {
        unsigned long c=integer(part,CPU_SETSIZE-1); if(CPU_ISSET(c,&wanted)) {errno=EINVAL;die("duplicate CPU");}
        CPU_SET(c,&wanted); count++;
    }
    if(!count || argv[1][0]==',' || argv[1][strlen(argv[1])-1]==',' || strstr(argv[1],",,")) {errno=EINVAL;die("empty CPU field");}
    unsigned long node=integer(argv[2],1), gib=integer(argv[3],12);
    if(!gib || argv[5][0]!='/') {errno=EINVAL;die("memory or interpreter path");}
    if(sched_setaffinity(0,sizeof(wanted),&wanted))die("sched_setaffinity");
    unsigned long mask=1UL<<node, observed=0; int mode=-1;
    if(syscall(SYS_set_mempolicy,MPOL_BIND,&mask,64UL))die("set_mempolicy");
    if(prctl(PR_SET_THP_DISABLE,1UL,0UL,0UL,0UL))die("PR_SET_THP_DISABLE");
    struct rlimit memory={gib*1024UL*1024UL*1024UL,gib*1024UL*1024UL*1024UL}, cpu={1800,1800};
    if(setrlimit(RLIMIT_AS,&memory) || setrlimit(RLIMIT_CPU,&cpu))die("setrlimit");
    if(sched_getaffinity(0,sizeof(actual),&actual) || !CPU_EQUAL(&wanted,&actual)) {errno=EINVAL;die("verify affinity");}
    if(syscall(SYS_get_mempolicy,&mode,&observed,64UL,NULL,0UL) || mode!=MPOL_BIND || observed!=mask) {errno=EINVAL;die("verify memory policy");}
    if(prctl(PR_GET_THP_DISABLE,0UL,0UL,0UL,0UL)!=1) {errno=EINVAL;die("verify THP");}
    char cpus[8192]={0};size_t offset=0;
    for(int c=0;c<CPU_SETSIZE;c++) if(CPU_ISSET(c,&actual)) {
        int size=snprintf(cpus+offset,sizeof(cpus)-offset,"%s%d",offset?",":"",c);
        if(size<0 || (size_t)size>=sizeof(cpus)-offset) {errno=EOVERFLOW;die("CPU evidence");} offset+=(size_t)size;
    }
    struct rusage u;if(getrusage(RUSAGE_SELF,&u))die("getrusage");
    struct timespec ts;if(clock_gettime(CLOCK_MONOTONIC,&ts))die("clock_gettime");
    char evidence[12288];int n=snprintf(evidence,sizeof(evidence),
        "{\"version\":1,\"pid\":%ld,\"cpus\":[%s],\"memory_policy\":%d,\"node_mask\":%lu,\"thp_disabled\":true,\"memory_limit_bytes\":%lu,\"cpu_limit_s\":1800,\"major_faults\":%ld,\"block_input_ops\":%ld,\"monotonic_ns\":%lld}",
        (long)getpid(),cpus,mode,observed,(unsigned long)memory.rlim_cur,u.ru_majflt,u.ru_inblock,(long long)ts.tv_sec*1000000000LL+ts.tv_nsec);
    if(n<0 || n>=(int)sizeof(evidence)) {errno=EOVERFLOW;die("launcher evidence");}
    if(setenv("LEASEPATH_PREEXEC_V1",evidence,1))die("setenv");
    free(list);execv(argv[5],argv+5);die("execv");
}
