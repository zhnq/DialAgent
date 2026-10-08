// macOS-only synthetic audio probe. Only the two named demo devices are allowed.
// Build: clang scripts/virtual_audio_probe.c -o work/mac-preflight/audio-probe
//        -framework AudioToolbox -framework CoreAudio -framework CoreFoundation
#include <AudioToolbox/AudioToolbox.h>
#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct {
    double frequency, expected, real, imag, other_real, other_imag, power;
    unsigned long long generated, received;
    unsigned window_frames, windows, matched;
    double tail_rms[2];
    int bridge;
} Probe;

static void check(OSStatus status, const char *operation) {
    if (status != noErr) {
        fprintf(stderr, "%s failed: %d\n", operation, (int)status);
        exit(2);
    }
}

static CFStringRef device_uid(const char *wanted) {
    AudioObjectPropertyAddress address = {
        kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    UInt32 size = 0;
    check(AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &address,
                                        0, NULL, &size), "list devices size");
    AudioDeviceID *devices = malloc(size);
    check(AudioObjectGetPropertyData(kAudioObjectSystemObject, &address,
                                    0, NULL, &size, devices), "list devices");
    for (unsigned i = 0; i < size / sizeof(AudioDeviceID); ++i) {
        address.mSelector = kAudioObjectPropertyName;
        CFStringRef name = NULL;
        UInt32 property_size = sizeof(name);
        if (AudioObjectGetPropertyData(devices[i], &address, 0, NULL,
                                      &property_size, &name) != noErr) continue;
        char label[512] = {0};
        CFStringGetCString(name, label, sizeof(label), kCFStringEncodingUTF8);
        CFRelease(name);
        if (strcmp(label, wanted)) continue;
        address.mSelector = kAudioDevicePropertyDeviceUID;
        CFStringRef uid = NULL;
        property_size = sizeof(uid);
        check(AudioObjectGetPropertyData(devices[i], &address, 0, NULL,
                                        &property_size, &uid), "device UID");
        free(devices);
        return uid;
    }
    free(devices);
    fprintf(stderr, "Device not found: %s\n", wanted);
    exit(2);
}

static void output(void *context, AudioQueueRef queue, AudioQueueBufferRef buffer) {
    Probe *probe = context;
    float *samples = buffer->mAudioData;
    unsigned frames = buffer->mAudioDataBytesCapacity / (2 * sizeof(float));
    for (unsigned i = 0; i < frames; ++i) {
        float value = 0.12 * sin(2 * M_PI * probe->frequency * probe->generated++ / 48000);
        samples[2 * i] = samples[2 * i + 1] = value;
    }
    buffer->mAudioDataByteSize = frames * 2 * sizeof(float);
    AudioQueueEnqueueBuffer(queue, buffer, 0, NULL);
}

static void input(void *context, AudioQueueRef queue, AudioQueueBufferRef buffer,
                  const AudioTimeStamp *time, UInt32 packets,
                  const AudioStreamPacketDescription *descriptions) {
    (void)time; (void)packets; (void)descriptions;
    Probe *probe = context;
    float *samples = buffer->mAudioData;
    unsigned frames = buffer->mAudioDataByteSize / (2 * sizeof(float));
    for (unsigned i = 0; i < frames; ++i) {
        double value = samples[2 * i];
        double phase = 2 * M_PI * probe->expected * probe->received / 48000;
        double other_phase = 2 * M_PI * probe->frequency * probe->received++ / 48000;
        probe->real += value * cos(phase);
        probe->imag += value * sin(phase);
        probe->other_real += value * cos(other_phase);
        probe->other_imag += value * sin(other_phase);
        probe->power += value * value;
        if (probe->bridge && ++probe->window_frames == 48000) {
            double amplitude = 2 * hypot(probe->real, probe->imag) / 48000;
            double other = 2 * hypot(probe->other_real, probe->other_imag) / 48000;
            double rms = sqrt(probe->power / 48000);
            probe->matched += amplitude > 0.03 && amplitude > 5 * other;
            probe->tail_rms[probe->windows++ % 2] = rms;
            probe->real = probe->imag = probe->power = 0;
            probe->other_real = probe->other_imag = 0;
            probe->window_frames = 0;
        }
    }
    AudioQueueEnqueueBuffer(queue, buffer, 0, NULL);
}

static int allowed(const char *name) {
    return !strcmp(name, "Phone-to-Codex") || !strcmp(name, "Codex-to-Phone");
}

int main(int argc, char **argv) {
    int bridge = argc == 4 && !strcmp(argv[3], "--bridge");
    if ((argc != 3 && !bridge) || !allowed(argv[1]) || !allowed(argv[2]) ||
        (bridge && (strcmp(argv[1], "Codex-to-Phone") || strcmp(argv[2], "Phone-to-Codex")))) {
        fprintf(stderr, "Usage: audio-probe <demo output> <demo input> [--bridge]\n");
        return 2;
    }
    // Do not leave a test process running if an audio service stops responding.
    alarm(25);
    CFStringRef out_uid = device_uid(argv[1]), in_uid = device_uid(argv[2]);
    Probe probe = {.frequency = bridge ? 697 : 997,
                   .expected = bridge ? 1209 : 997, .bridge = bridge};
    AudioStreamBasicDescription format = {
        .mSampleRate = 48000, .mFormatID = kAudioFormatLinearPCM,
        .mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked,
        .mBytesPerPacket = 8, .mFramesPerPacket = 1, .mBytesPerFrame = 8,
        .mChannelsPerFrame = 2, .mBitsPerChannel = 32
    };
    AudioQueueRef in_queue = NULL, out_queue = NULL;
    check(AudioQueueNewInput(&format, input, &probe, NULL, NULL, 0, &in_queue), "input queue");
    check(AudioQueueSetProperty(in_queue, kAudioQueueProperty_CurrentDevice,
                               &in_uid, sizeof(in_uid)), "input device");
    check(AudioQueueNewOutput(&format, output, &probe, NULL, NULL, 0, &out_queue), "output queue");
    check(AudioQueueSetProperty(out_queue, kAudioQueueProperty_CurrentDevice,
                               &out_uid, sizeof(out_uid)), "output device");
    for (int i = 0; i < 3; ++i) {
        AudioQueueBufferRef buffer;
        check(AudioQueueAllocateBuffer(in_queue, 2048 * 8, &buffer), "input buffer");
        check(AudioQueueEnqueueBuffer(in_queue, buffer, 0, NULL), "enqueue input");
        check(AudioQueueAllocateBuffer(out_queue, 2048 * 8, &buffer), "output buffer");
        output(&probe, out_queue, buffer);
    }
    check(AudioQueueStart(in_queue, NULL), "start input");
    check(AudioQueueStart(out_queue, NULL), "start output");
    if (bridge) {
        puts("PROBE_READY");
        fflush(stdout);
        usleep(12000000);
    } else {
        usleep(2000000);
    }
    check(AudioQueueStop(out_queue, true), "stop output");
    check(AudioQueueStop(in_queue, true), "stop input");
    AudioQueueDispose(out_queue, true);
    AudioQueueDispose(in_queue, true);
    CFRelease(out_uid); CFRelease(in_uid);
    if (bridge) {
        int quiet_tail = probe.windows >= 10 &&
                         probe.tail_rms[0] < 0.001 && probe.tail_rms[1] < 0.001;
        int passed = probe.matched >= 3 && quiet_tail;
        printf("{\"scope\":\"SIP bridge synthetic audio\",\"frames\":%llu,"
               "\"windows\":%u,\"matched_seconds\":%u,\"expected_hz\":1209,"
               "\"generated_hz\":697,\"quiet_after_hangup\":%s,"
               "\"tail_rms\":[%.6f,%.6f],\"passed\":%s}\n",
               probe.received, probe.windows, probe.matched,
               quiet_tail ? "true" : "false", probe.tail_rms[0], probe.tail_rms[1],
               passed ? "true" : "false");
        return passed ? 0 : 1;
    }
    double amplitude = probe.received ? 2 * hypot(probe.real, probe.imag) / probe.received : 0;
    double rms = probe.received ? sqrt(probe.power / probe.received) : 0;
    int same_device = !strcmp(argv[1], argv[2]);
    int passed = probe.received >= 48000 &&
        (same_device ? (amplitude > 0.04) : (rms < 0.001));
    printf("{\"output\":\"%s\",\"input\":\"%s\",\"frames\":%llu,"
           "\"tone_amplitude\":%.6f,\"rms\":%.6f,\"passed\":%s}\n",
           argv[1], argv[2], probe.received, amplitude, rms, passed ? "true" : "false");
    return passed ? 0 : 1;
}
