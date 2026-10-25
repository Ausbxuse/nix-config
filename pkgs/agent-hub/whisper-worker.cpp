// One CPU model per hub, private pipes only. No audio or transcripts on disk.
#include <whisper.h>
#include <ggml-backend.h>
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include <unistd.h>

static void quiet(ggml_log_level, const char *, void *) {}

static bool read_size(uint32_t &size) {
    std::array<unsigned char, 4> bytes;
    if (std::fread(bytes.data(), 1, 4, stdin) != 4) return false;
    size = uint32_t(bytes[0]) | uint32_t(bytes[1]) << 8 |
           uint32_t(bytes[2]) << 16 | uint32_t(bytes[3]) << 24;
    return true;
}

static bool reply(const std::string &text) {
    uint32_t size = text.size();
    std::array<unsigned char, 4> bytes = {static_cast<unsigned char>(size),
        static_cast<unsigned char>(size >> 8), static_cast<unsigned char>(size >> 16),
        static_cast<unsigned char>(size >> 24)};
    return std::fwrite(bytes.data(), 1, 4, stdout) == 4 &&
           std::fwrite(text.data(), 1, size, stdout) == size && std::fflush(stdout) == 0;
}

int main(int argc, char **argv) {
    if (argc != 3) return 1;
    // Pipe EOF ends idle workers. During inference, abort if the hub process
    // exits. PDEATHSIG follows the spawning HTTP thread on Linux, so it would
    // incorrectly kill this resident model whenever that request finishes.
    auto parent = getppid();
    if (parent == 1) return 1;
    whisper_log_set(quiet, nullptr);
    ggml_log_set(quiet, nullptr);
    ggml_backend_load_all();
    auto context_params = whisper_context_default_params();
    context_params.use_gpu = false;
    auto *context = whisper_init_from_file_with_params(argv[1], context_params);
    if (!context) return 2;

    // Match the previous CLI decoder; do not carry prompts between utterances.
    auto params = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
    params.n_threads = std::clamp(std::atoi(argv[2]), 1, 8);
    params.n_max_text_ctx = 0;
    params.no_context = true;
    params.no_timestamps = true;
    params.print_realtime = params.print_progress = params.print_timestamps = false;
    params.language = "en";
    params.greedy.best_of = 1;
    params.temperature_inc = 0;
    params.suppress_nst = true;
    params.abort_callback = [](void *data) { return getppid() != *static_cast<pid_t *>(data); };
    params.abort_callback_user_data = &parent;
    std::fwrite("HVW1", 1, 4, stdout);
    std::fflush(stdout);

    uint32_t count;
    while (read_size(count) && count > 0 && count <= 30 * WHISPER_SAMPLE_RATE) {
        std::vector<unsigned char> pcm(count * 2);
        if (std::fread(pcm.data(), 1, pcm.size(), stdin) != pcm.size()) break;
        std::vector<float> samples(count);
        for (uint32_t i = 0; i < count; ++i) {
            int value = pcm[2 * i] | int(pcm[2 * i + 1]) << 8;
            if (value >= 32768) value -= 65536;
            samples[i] = value / 32768.0f;
        }
        if (whisper_full(context, params, samples.data(), count) != 0) break;
        std::string text;
        for (int i = 0; i < whisper_full_n_segments(context); ++i) {
            text += whisper_full_get_segment_text(context, i);
        }
        if (text.size() > 48000 || !reply(text)) break;
    }
    whisper_free(context);
    return 0;
}
