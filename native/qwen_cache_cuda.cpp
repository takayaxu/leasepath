// Same pinned CUDA engine/model; sequence 0 executes, 1..capacity own blocks.
// seq_cp shares physical cells. Removing the last reference frees used cells;
// preallocated device KV buffer bytes do not shrink and are reported separately.
#include "qwen_data_cuda.cpp"
#include <set>

struct MultiEngine : Engine {
    unsigned capacity, block, headroom;
    MultiEngine(const char *path, unsigned context, unsigned batch, int threads,
                unsigned gpus, unsigned capacity_, unsigned block_, unsigned headroom_)
        : Engine(path, context, batch, threads, gpus, capacity_ + 1),
          capacity(capacity_), block(block_), headroom(headroom_) {
        require(capacity > 0 && capacity <= 254 && block > 0, "invalid cache geometry");
        require(context >= capacity * block + headroom && headroom >= 1152, "insufficient physical headroom");
    }

    J sequence(int sequence_id) const {
        const auto &slots = kv->get_cells(0);
        J result = J::array();
        for (const auto &[position, index] : slots.seq_pos_get(sequence_id)) {
            require(slots.seq_has(index, sequence_id), "sequence membership inconsistent");
            result.push_back({position, index, slots.ext_get(index).tok});
        }
        return result;
    }

    J inspect() const {
        llama_synchronize(ctx.get());
        J sequences = J::object();
        std::set<uint32_t> union_cells, idle_cells;
        unsigned references = 0;
        for (unsigned s = 0; s <= capacity; ++s) {
            auto cells = sequence(s);
            if (cells.empty()) { continue; }
            sequences[std::to_string(s)] = cells;
            for (auto &row : cells) {
                union_cells.insert(row[1].get<uint32_t>());
                if (s) { idle_cells.insert(row[1].get<uint32_t>()); }
                ++references;
            }
        }
        require(union_cells.size() == kv->get_cells(0).get_used(), "unmapped used physical KV cells");
        require(idle_cells.size() <= capacity * block, "idle payload exceeds quota");
        return {{"sequences", sequences}, {"used_cells", union_cells.size()},
                {"idle_union_cells", idle_cells.size()}, {"sequence_references", references},
                {"reserved_capacity_cells", kv->get_size()}, {"device_kv_reserved_bytes", memory()}};
    }

    void drop(const J &slots) {
        require(!active, "cannot evict during active execution");
        for (const auto &entry : slots) {
            int slot = entry.get<int>();
            require(slot > 0 && unsigned(slot) <= capacity, "invalid block sequence");
        }
        for (const auto &entry : slots) {
            require(llama_memory_seq_rm(llama_get_memory(ctx.get()), entry.get<int>(), -1, -1), "block removal failed");
        }
    }

    J request_multi(const char *prompt, int bytes, unsigned limit, int64_t deadline, const J &plan) {
        require(!active && sequence(0).empty(), "active sequence was not drained");
        const auto enqueued = now();
        const auto tokens = tokenize(prompt, bytes);
        const auto wanted = plan.at("slots").get<std::vector<int>>();
        const auto stops = plan.at("stops");
        require(limit > 0 && limit <= 1024 && tokens.size() + limit <= llama_n_ctx(ctx.get()), "request bounds");
        require(tokens.size() - wanted.size() * block <= block &&
                wanted.size() == (tokens.size()-1)/block && wanted.size() <= capacity, "invalid prefix block plan");
        require(std::set<int>(wanted.begin(), wanted.end()).size() == wanted.size(), "duplicate slot");
        for (auto slot : wanted) { require(slot > 0 && unsigned(slot) <= capacity, "slot outside pool"); }
        // Validate every prospective hit from device metadata before mutating KV.
        size_t hit_blocks = 0;
        bool missing = false;
        for (size_t i = 0; i < wanted.size(); ++i) {
            auto stored = sequence(wanted[i]);
            if (stored.empty()) { missing = true; continue; }
            require(!missing && stored.size() == block, "non-prefix cached suffix");
            for (unsigned j = 0; j < block; ++j) {
                require(stored[j][0] == i*block+j && stored[j][2] == tokens[i*block+j], "cached KV identity differs");
            }
            ++hit_blocks;
        }
        const size_t hit = hit_blocks * block;
        const auto dispatched = now();
        active = true;
        for (size_t i = 0; i < hit_blocks; ++i) {
            llama_memory_seq_cp(llama_get_memory(ctx.get()), wanted[i], 0, i*block, (i+1)*block);
        }
        auto lookup_cells = sequence(0);
        require(lookup_cells.size() == hit, "failed to attach cached prefix");
        const auto lookup = now();
        J result = {{"backend_id", ++seq}, {"tokens", tokens}, {"hit", hit},
                    {"enqueue_ns", enqueued}, {"dispatch_ns", dispatched}, {"lookup_ns", lookup},
                    {"lookup_cells", lookup_cells}, {"before", inspect()}};
        size_t count = hit;
        std::string reason = "length", text, matched;
        Tokens outputs;
        auto interrupted = [&]() {
            if (cancelled.load()) { reason = "cancelled"; return true; }
            if (now() >= deadline) { reason = "timeout"; return true; }
            return false;
        };
        const auto prefill_start = now();
        while (count < tokens.size() && !interrupted()) {
            unsigned n = std::min<size_t>(batch_size, tokens.size()-count);
            if (decode(tokens.data()+count, count, n)) { reason = "engine_error"; break; }
            count += n;
        }
        llama_synchronize(ctx.get());
        const auto prefill_end = now();
        int64_t first_token_ns = 0;
        if (count == tokens.size() && reason == "length") {
            for (unsigned i = 0; i < limit && !interrupted(); ++i) {
                auto token = llama_sampler_sample(sampler.get(), ctx.get(), -1);
                if (!first_token_ns) { first_token_ns = now(); }
                if (llama_vocab_is_eog(vocab, token)) { reason = "eog"; break; }
                char piece[1024];
                int n = llama_token_to_piece(vocab, token, piece, sizeof(piece), 0, false);
                if (n < 0) { reason = "engine_error"; break; }
                outputs.push_back(token);
                text.append(piece, n);
                size_t first = std::string::npos;
                for (const auto &entry : stops) {
                    const auto stop = entry.get<std::string>();
                    if (auto pos = text.find(stop); pos < first) { first = pos; matched = stop; }
                }
                if (first != std::string::npos) { text.resize(first); reason = "protocol_stop"; break; }
                if (i+1 < limit && decode(&token, tokens.size()+i, 1)) { reason = "engine_error"; break; }
            }
        }
        llama_synchronize(ctx.get());
        const auto decode_end = now();
        if (reason != "engine_error" && reason != "cancelled" && reason != "timeout") { interrupted(); }
        result["prefill"] = count-hit;
        result["peak"] = inspect();
        require(result["peak"]["used_cells"].get<unsigned>() <= capacity*block+headroom, "active payload exceeds quota");
        const bool completed = reason == "eog" || reason == "protocol_stop" || reason == "length";
        if (completed) {
            auto released = sequence(0);
            J input = J::array();
            for (auto &row : released) { if (row[0].get<size_t>() < tokens.size()) { input.push_back(row); } }
            require(input.size() == tokens.size(), "incomplete release input cells");
            for (size_t i = 0; i < input.size(); ++i) {
                require(input[i][0] == i && input[i][2] == tokens[i], "wrong physical release input");
                if (i < hit) { require(input[i] == lookup_cells[i], "reused physical cell changed"); }
            }
            for (size_t i = hit_blocks; i < wanted.size(); ++i) {
                require(sequence(wanted[i]).empty(), "new slot is occupied");
                llama_memory_seq_cp(llama_get_memory(ctx.get()), 0, wanted[i], i*block, (i+1)*block);
            }
            result["release_cells"] = input;
        } else { result["release_cells"] = J::array(); }
        require(llama_memory_seq_rm(llama_get_memory(ctx.get()), 0, -1, -1), "active sequence drain failed");
        active = false;
        cancelled.store(false);
        result["after"] = inspect();
        result["release_ns"] = now();
        result["reason"] = reason;
        result["text"] = text;
        result["output_tokens"] = outputs;
        result["matched_stop"] = matched;
        result["prefill_ns"] = prefill_end-prefill_start;
        result["decode_ns"] = decode_end-prefill_end;
        result["first_token_ns"] = first_token_ns;
        return result;
    }
};

extern "C" {
MultiEngine *lp_qm_open(const char *path, unsigned context, unsigned batch, int threads,
                      unsigned gpus, unsigned capacity, unsigned block, unsigned headroom) {
    try { return new MultiEngine(path, context, batch, threads, gpus, capacity, block, headroom); }
    catch (const std::exception &e) { failure = e.what(); return nullptr; }
}
void lp_qm_close(MultiEngine *engine) { delete engine; llama_backend_free(); }
const char *lp_qm_inspect(MultiEngine *engine) {
    try { engine->response = engine->inspect().dump(); return engine->response.c_str(); }
    catch (const std::exception &e) { failure = e.what(); return nullptr; }
}
const char *lp_qm_drop(MultiEngine *engine, const char *slots) {
    try { engine->drop(J::parse(slots)); engine->response = engine->inspect().dump(); return engine->response.c_str(); }
    catch (const std::exception &e) { failure = e.what(); return nullptr; }
}
const char *lp_qm_generate(MultiEngine *engine, const char *prompt, int bytes,
                         unsigned limit, int64_t deadline, const char *plan) {
    try { engine->response = engine->request_multi(prompt, bytes, limit, deadline, J::parse(plan)).dump(); return engine->response.c_str(); }
    catch (const std::exception &e) {
        failure = e.what();
        // Fatal adapter errors invalidate all metadata; caller must reset its controller.
        llama_memory_clear(llama_get_memory(engine->ctx.get()), false);
        engine->active = false; engine->cancelled.store(false);
        return nullptr;
    }
}
}
