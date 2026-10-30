// Thin, local llama.cpp b7017 bridge. Block owners are native sequence IDs;
// seq_cp adds references to the same device KV cells, never host save/restore.
#include "llama.h"
#include "ggml-backend.h"
#include "llama-context.h"
#include "llama-kv-cache.h"
#include <algorithm>
#include <chrono>
#include <cstring>
#include <exception>
#include <string>
#include <stdexcept>
#include <vector>

static thread_local std::string error;
struct Engine { llama_model * model=nullptr; llama_context * ctx=nullptr; };
static void quiet(ggml_log_level level,const char * text,void *) {
    if (level>=GGML_LOG_LEVEL_WARN) fputs(text,stderr);
}
extern "C" {
const char * lp_error() { return error.c_str(); }
void * lp_open(const char * path,int cells,int threads) {
    try {
        llama_log_set(quiet,nullptr);ggml_backend_load_all();llama_backend_init();
        auto * e=new Engine;
        auto mp=llama_model_default_params();mp.n_gpu_layers=999;
        e->model=llama_model_load_from_file(path,mp);
        if (!e->model) { delete e;throw std::runtime_error("model load failed"); }
        auto cp=llama_context_default_params();cp.n_ctx=cells;cp.n_batch=512;cp.n_ubatch=128;
        cp.n_seq_max=256;cp.kv_unified=true;cp.n_threads=threads;cp.n_threads_batch=threads;
        cp.type_k=GGML_TYPE_F16;cp.type_v=GGML_TYPE_F16;cp.no_perf=false;
        cp.flash_attn_type=LLAMA_FLASH_ATTN_TYPE_DISABLED;
        e->ctx=llama_init_from_model(e->model,cp);
        if (!e->ctx) {llama_model_free(e->model);delete e;throw std::runtime_error("context load failed");}
        return e;
    } catch (const std::exception & x) { error=x.what();return nullptr; }
}
void lp_close(void * p) { auto * e=(Engine*)p;if(e){llama_free(e->ctx);llama_model_free(e->model);delete e;} }
void lp_clear(void * p) {auto * e=(Engine*)p;llama_memory_clear(llama_get_memory(e->ctx),true);}
int lp_drop(void * p,int seq) {auto * e=(Engine*)p;return llama_memory_seq_rm(llama_get_memory(e->ctx),seq,-1,-1);}
void lp_copy(void * p,int src,int dst,int lo,int hi) {auto * e=(Engine*)p;llama_memory_seq_cp(llama_get_memory(e->ctx),src,dst,lo,hi);}
int lp_tokenize(void * p,const char * text,int size,int32_t * out,int capacity) {
    auto * e=(Engine*)p;return llama_tokenize(llama_model_get_vocab(e->model),text,size,out,capacity,false,true);
}
int lp_detokenize(void * p,const int32_t * tokens,int count,char * out,int capacity) {
    auto * e=(Engine*)p;return llama_detokenize(llama_model_get_vocab(e->model),tokens,count,out,capacity,false,true);
}
int lp_usage(void * p,int active,llama_leasepath_kv_usage * out) {
    auto * e=(Engine*)p;llama_synchronize(e->ctx);
    llama_seq_id seq=0;return llama_leasepath_memory_usage(llama_get_memory(e->ctx),&seq,active?1:0,out);
}
int lp_span(void * p) {
    auto * e=(Engine*)p;auto * cache=dynamic_cast<llama_kv_cache*>(e->ctx->get_memory());
    if(!cache)return -1;
    llama_kv_cache::slot_info info;info.s0=0;info.s1=1;info.resize(1);info.strm[0]=0;info.idxs[0]={0};
    return cache->get_n_kv(info);
}
// Times include native decode/sampling/synchronization; Python control and
// resource scans are measured separately. Exactly n_predict samples are made.
int lp_run(void * p,const int32_t * tokens,int count,int cached,int predictions,int32_t * outputs,double * timing) {
    try {
        auto * e=(Engine*)p;
        if(cached<0||cached>=count||predictions<1)throw std::runtime_error("invalid prompt/cache length");
        llama_synchronize(e->ctx);llama_perf_context_reset(e->ctx);
        auto begin=std::chrono::steady_clock::now();
        auto batch=llama_batch_init(512,0,1);
        for(int lo=cached;lo<count;lo+=512){
            batch.n_tokens=std::min(512,count-lo);
            for(int i=0;i<batch.n_tokens;++i){batch.token[i]=tokens[lo+i];batch.pos[i]=lo+i;
                batch.n_seq_id[i]=1;batch.seq_id[i][0]=0;batch.logits[i]=(lo+i==count-1);}
            int code=llama_decode(e->ctx,batch);
            if(code!=0){llama_batch_free(batch);throw std::runtime_error("prompt decode failed: "+std::to_string(code));}
        }
        llama_synchronize(e->ctx);auto prompt_end=std::chrono::steady_clock::now();
        auto * sampler=llama_sampler_init_greedy();
        for(int i=0;i<predictions;++i){
            outputs[i]=llama_sampler_sample(sampler,e->ctx,-1);
            if(i+1<predictions){batch.n_tokens=1;batch.token[0]=outputs[i];batch.pos[0]=count+i;
                batch.n_seq_id[0]=1;batch.seq_id[0][0]=0;batch.logits[0]=true;
                int code=llama_decode(e->ctx,batch);
                if(code!=0){llama_sampler_free(sampler);llama_batch_free(batch);throw std::runtime_error("generation decode failed");}}
        }
        llama_synchronize(e->ctx);auto end=std::chrono::steady_clock::now();
        llama_sampler_free(sampler);llama_batch_free(batch);
        auto perf=llama_perf_context(e->ctx);
        timing[0]=std::chrono::duration<double,std::milli>(prompt_end-begin).count();
        timing[1]=std::chrono::duration<double,std::milli>(end-prompt_end).count();
        timing[2]=perf.n_p_eval;timing[3]=perf.n_eval;
        return 0;
    }catch(const std::exception & x){error=x.what();return -1;}
}
}
