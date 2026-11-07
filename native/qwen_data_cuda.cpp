// ABI pinned to llama.cpp fd27b2034905f8dadd8bd6d2cc44595f4929d391.
// One engine thread/sequence, no context shift, speculative decode or preemption.
#include "llama.h"
#include "llama-kv-cache.h"
#include "llama-kv-cells.h"
#include "ggml-backend.h"
#include "json.hpp"

#include <algorithm>
#include <atomic>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <time.h>
#include <vector>

using J = nlohmann::json;
using Tokens = std::vector<llama_token>;

static int64_t now() {
    timespec ts{};
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) { throw std::runtime_error("clock_gettime"); }
    return ts.tv_sec * 1000000000LL + ts.tv_nsec;
}
static void require(bool ok, const char *why) {
    if (!ok) { throw std::runtime_error(why); }
}

struct Engine {
    std::unique_ptr<llama_model, decltype(&llama_model_free)> model{nullptr, llama_model_free};
    std::unique_ptr<llama_context, decltype(&llama_free)> ctx{nullptr, llama_free};
    std::unique_ptr<llama_sampler, decltype(&llama_sampler_free)> sampler{nullptr, llama_sampler_free};
    const llama_vocab *vocab = nullptr;
    llama_kv_cache *kv = nullptr;
    std::atomic<bool> cancelled{false};
    uint64_t seq = 0;
    bool active = false;
    unsigned batch_size = 0;
    std::string response;

    Engine(const char *path, unsigned context, unsigned batch, int threads, unsigned gpus) {
        llama_backend_init();
        std::vector<ggml_backend_dev_t> devices;
        for (size_t i = 0; i < ggml_backend_dev_count(); ++i) {
            auto dev = ggml_backend_dev_get(i);
            if (ggml_backend_dev_type(dev) == GGML_BACKEND_DEVICE_TYPE_GPU &&
                std::string(ggml_backend_dev_name(dev)).rfind("CUDA", 0) == 0) {
                devices.push_back(dev);
            }
        }
        require(devices.size() == gpus && gpus > 0, "unexpected visible CUDA device set");
        devices.push_back(nullptr);
        auto mp = llama_model_default_params();
        mp.devices = devices.data();
        mp.n_gpu_layers = 999;
        mp.split_mode = LLAMA_SPLIT_MODE_LAYER;
        std::vector<float> split(gpus, 1.0f);
        mp.tensor_split = split.data();
        model.reset(llama_model_load_from_file(path, mp));
        require(bool(model), "model load failed");
        vocab = llama_model_get_vocab(model.get());
        auto cp = llama_context_default_params();
        cp.n_ctx = context;
        cp.n_batch = batch;
        cp.n_ubatch = std::min(batch, 128U);
        cp.n_seq_max = 1;
        cp.n_threads = cp.n_threads_batch = threads;
        cp.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
        cp.type_k = cp.type_v = GGML_TYPE_F16;
        cp.offload_kqv = true;
        cp.no_perf = true;
        ctx.reset(llama_init_from_model(model.get(), cp));
        require(bool(ctx), "context init failed");
        auto mem = llama_get_memory(ctx.get());
        kv = dynamic_cast<llama_kv_cache *>(mem);
        require(kv && typeid(*kv) == typeid(llama_kv_cache) && kv->get_n_stream() == 1 &&
                kv->type_k() == GGML_TYPE_F16 && kv->type_v() == GGML_TYPE_F16,
                "requires dense single-stream F16 KV");
        const auto layout = memory();
        require(layout.size() == gpus, "KV was not allocated on every configured GPU");
        sampler.reset(llama_sampler_init_greedy());
        require(bool(sampler), "sampler init failed");
        batch_size = batch;
    }

    J memory() const {
        J devices = J::object();
        for (auto [type, bytes] : kv->memory_breakdown()) {
            if (!bytes) { continue; }
            const auto dev = ggml_backend_buft_get_device(type);
            require(dev && !ggml_backend_buft_is_host(type), "host KV is unsupported");
            const std::string name = ggml_backend_dev_name(dev);
            require(name.rfind("CUDA", 0) == 0, "non-CUDA KV allocation");
            auto &value = devices[name];
            value = (value.is_null() ? 0ULL : value.get<uint64_t>()) + bytes;
        }
        return devices;
    }

    // Reads actual physical cell IDs, logical position and the token recorded by apply_ubatch.
    // No token correspondence is reconstructed from the caller's history.
    J cells(size_t prefix) const {
        auto const &slots = kv->get_cells(0);
        J result = J::array();
        size_t expected = 0;
        for (const auto &[position, index] : slots.seq_pos_get(0)) {
            require(position >= 0 && size_t(position) == expected++, "non-contiguous KV");
            if (size_t(position) < prefix) {
                require(slots.seq_has(index, 0) && slots.seq_count(index) == 1, "unexpected KV ownership");
                result.push_back({index, slots.ext_get(index).tok});
            }
        }
        require(expected == slots.get_used(), "unaccounted physical cells");
        return result;
    }

    Tokens tokenize(const char *prompt, int size) const {
        Tokens tokens(size + 16);
        int n = llama_tokenize(vocab, prompt, size, tokens.data(), tokens.size(), false, true);
        require(n > 0, "engine prompt tokenization failed");
        tokens.resize(n);
        return tokens;
    }

    void clear() {
        require(!active, "cannot clear an active request");
        llama_synchronize(ctx.get());
        llama_memory_clear(llama_get_memory(ctx.get()), false);
        require(cells(0).empty() && kv->get_cells(0).get_used() == 0, "clear did not release cells");
    }

    int decode(const llama_token *tokens, int start, int n) {
        auto batch = llama_batch_init(n, 0, 1);
        batch.n_tokens = n;
        for (int i = 0; i < n; ++i) {
            batch.token[i] = tokens[i];
            batch.pos[i] = start+i;
            batch.n_seq_id[i] = 1;
            batch.seq_id[i][0] = 0;
            batch.logits[i] = i == n-1;
        }
        int rc = llama_decode(ctx.get(), batch);
        llama_batch_free(batch);
        return rc;
    }

    J request(const char *prompt, int bytes, unsigned limit, int64_t deadline, const J &stops) {
        require(!active, "overlapping engine request");
        const auto id = ++seq;
        const auto enqueue = now();
        const auto tokens = tokenize(prompt, bytes);
        require(limit > 0 && tokens.size() + limit <= llama_n_ctx(ctx.get()), "engine context limit");
        const auto dispatch = now();
        active = true;
        const auto initial = cells(tokens.size());
        const auto lookup = now();
        size_t hit = 0;
        while (hit < initial.size() && hit + 1 < tokens.size() && initial[hit][1] == tokens[hit]) { ++hit; }

        J output = {{"backend_id", id}, {"enqueue_ns", enqueue}, {"dispatch_ns", dispatch},
                    {"lookup_ns", lookup}, {"tokens", tokens}, {"lookup_cells", initial},
                    {"hit", hit}, {"prefill", 0}, {"capacity_cells", kv->get_size()},
                    {"device_kv_bytes", memory()}};
        std::string reason = "length";
        std::string generated;
        Tokens out_ids;
        llama_token terminal_token = LLAMA_TOKEN_NULL;
        std::string matched_stop;
        size_t count = hit;
        auto interrupted = [&]() {
            if (cancelled.load()) { reason = "cancelled"; return true; }
            if (now() >= deadline) { reason = "timeout"; return true; }
            return false;
        };
        if (!llama_memory_seq_rm(llama_get_memory(ctx.get()), 0, hit, -1)) {
            reason = "engine_error";
        } else {
            while (count < tokens.size() && !interrupted()) {
                int n = std::min<size_t>(batch_size, tokens.size() - count);
                if (decode(tokens.data()+count, count, n)) { reason = "engine_error"; break; }
                count += n;
            }
            if (count == tokens.size() && reason == "length") {
                for (unsigned i = 0; i < limit && !interrupted(); ++i) {
                    auto token = llama_sampler_sample(sampler.get(), ctx.get(), -1);
                    if (llama_vocab_is_eog(vocab, token)) { terminal_token = token; reason = "eog"; break; }
                    char piece[1024];
                    int n = llama_token_to_piece(vocab, token, piece, sizeof(piece), 0, false);
                    if (n < 0) { reason = "engine_error"; break; }
                    out_ids.push_back(token);
                    generated.append(piece, n);
                    size_t first = std::string::npos;
                    for (const auto &entry : stops) {
                        const auto stop = entry.get<std::string>();
                        if (auto pos = generated.find(stop); pos < first) { first = pos; matched_stop = stop; }
                    }
                    if (first != std::string::npos) { generated.resize(first); reason = "protocol_stop"; break; }
                    if (i + 1 < limit && decode(&token, tokens.size() + i, 1)) {
                        reason = "engine_error"; break;
                    }
                }
            }
        }
        llama_synchronize(ctx.get());
        if (reason != "engine_error" && reason != "cancelled" && reason != "timeout") { interrupted(); }
        output["prefill"] = count - hit;
        if (reason == "eog" || reason == "protocol_stop" || reason == "length") {
            auto release = cells(tokens.size());
            require(release.size() == tokens.size(), "release missing input cells");
            for (size_t i = 0; i < release.size(); ++i) {
                require(release[i][1] == tokens[i], "release KV token mismatch");
                if (i < hit) { require(release[i] == initial[i], "claimed hit moved or changed"); }
            }
            output["release_cells"] = std::move(release);
        } else {
            llama_memory_clear(llama_get_memory(ctx.get()), false);
            require(kv->get_cells(0).get_used() == 0, "failed to drain rejected request");
            output["release_cells"] = J::array();
        }
        active = false;
        cancelled.store(false);
        output["release_ns"] = now();
        output["reason"] = reason;
        output["terminal_token"] = terminal_token;
        output["matched_stop"] = matched_stop;
        output["output_tokens"] = out_ids;
        output["text"] = generated;
        return output;
    }
};

static thread_local std::string failure;
extern "C" {
const char *lp_qd_error() { return failure.c_str(); }
Engine *lp_qd_open(const char *path, unsigned context, unsigned batch, int threads, unsigned gpus) {
    try { return new Engine(path, context, batch, threads, gpus); }
    catch (const std::exception &e) { failure = e.what(); return nullptr; }
}
void lp_qd_close(Engine *engine) { delete engine; llama_backend_free(); }
void lp_qd_cancel(Engine *engine) { engine->cancelled.store(true); }
int lp_qd_clear(Engine *engine) {
    try { engine->clear(); return 0; }
    catch (const std::exception &e) { failure = e.what(); return -1; }
}
const char *lp_qd_generate(Engine *engine, const char *prompt, int bytes, unsigned limit,
                           int64_t deadline, const char *stops) {
    try {
        engine->response = engine->request(prompt, bytes, limit, deadline, J::parse(stops))
                               .dump(-1, ' ', false, J::error_handler_t::replace);
        return engine->response.c_str();
    } catch (const std::exception &e) { failure = e.what(); return nullptr; }
}
}
