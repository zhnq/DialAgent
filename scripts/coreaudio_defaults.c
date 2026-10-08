// Read and change macOS default audio devices without opening an audio stream.
#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static AudioObjectPropertySelector default_selector(const char *direction) {
    if (strcmp(direction, "input") == 0) {
        return kAudioHardwarePropertyDefaultInputDevice;
    }
    if (strcmp(direction, "output") == 0) {
        return kAudioHardwarePropertyDefaultOutputDevice;
    }
    return 0;
}

static AudioObjectPropertyScope device_scope(const char *direction) {
    return strcmp(direction, "input") == 0
        ? kAudioDevicePropertyScopeInput
        : kAudioDevicePropertyScopeOutput;
}

static int device_name(AudioDeviceID device, char *buffer, size_t capacity) {
    AudioObjectPropertyAddress address = {
        kAudioObjectPropertyName,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    CFStringRef name = NULL;
    UInt32 size = sizeof(name);
    OSStatus status = AudioObjectGetPropertyData(device, &address, 0, NULL,
                                                 &size, &name);
    if (status != noErr || name == NULL) return 0;
    Boolean converted = CFStringGetCString(name, buffer, (CFIndex)capacity,
                                           kCFStringEncodingUTF8);
    CFRelease(name);
    return converted ? 1 : 0;
}

static UInt32 channel_count(AudioDeviceID device, AudioObjectPropertyScope scope) {
    AudioObjectPropertyAddress address = {
        kAudioDevicePropertyStreamConfiguration,
        scope,
        kAudioObjectPropertyElementMain
    };
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(device, &address, 0, NULL, &size) != noErr ||
        size == 0) return 0;
    AudioBufferList *list = malloc(size);
    if (list == NULL) return 0;
    if (AudioObjectGetPropertyData(device, &address, 0, NULL, &size, list) != noErr) {
        free(list);
        return 0;
    }
    UInt32 channels = 0;
    for (UInt32 index = 0; index < list->mNumberBuffers; ++index) {
        channels += list->mBuffers[index].mNumberChannels;
    }
    free(list);
    return channels;
}

static int current_default(const char *direction, AudioDeviceID *device) {
    AudioObjectPropertySelector selector = default_selector(direction);
    if (selector == 0) return 0;
    AudioObjectPropertyAddress address = {
        selector,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    UInt32 size = sizeof(*device);
    return AudioObjectGetPropertyData(kAudioObjectSystemObject, &address, 0,
                                      NULL, &size, device) == noErr;
}

static int set_default(const char *direction, AudioDeviceID device) {
    AudioObjectPropertySelector selector = default_selector(direction);
    if (selector == 0 || channel_count(device, device_scope(direction)) == 0) {
        return 0;
    }
    AudioObjectPropertyAddress address = {
        selector,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    UInt32 size = sizeof(device);
    return AudioObjectSetPropertyData(kAudioObjectSystemObject, &address, 0,
                                      NULL, size, &device) == noErr;
}

static int find_named(const char *direction, const char *wanted,
                      AudioDeviceID *matched) {
    AudioObjectPropertyAddress address = {
        kAudioHardwarePropertyDevices,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &address, 0,
                                       NULL, &size) != noErr || size == 0) return 0;
    AudioDeviceID *devices = malloc(size);
    if (devices == NULL) return 0;
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &address, 0, NULL,
                                   &size, devices) != noErr) {
        free(devices);
        return 0;
    }
    UInt32 count = size / sizeof(AudioDeviceID);
    int found = 0;
    for (UInt32 index = 0; index < count; ++index) {
        char name[1024];
        if (channel_count(devices[index], device_scope(direction)) > 0 &&
            device_name(devices[index], name, sizeof(name)) &&
            strcmp(name, wanted) == 0) {
            if (found) {
                fprintf(stderr, "Ambiguous %s device name: %s\n", direction, wanted);
                free(devices);
                return 0;
            }
            *matched = devices[index];
            found = 1;
        }
    }
    free(devices);
    return found;
}

static int print_default(const char *direction) {
    AudioDeviceID device = kAudioObjectUnknown;
    char name[1024];
    if (!current_default(direction, &device) ||
        !device_name(device, name, sizeof(name))) return 0;
    printf("%s\t%u\t%s\n", strcmp(direction, "input") == 0 ? "INPUT" : "OUTPUT",
           (unsigned)device, name);
    return 1;
}

static int parsed_device_id(const char *value, AudioDeviceID *device) {
    errno = 0;
    char *end = NULL;
    unsigned long number = strtoul(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || number > UINT32_MAX) return 0;
    *device = (AudioDeviceID)number;
    return 1;
}

static void usage(const char *program) {
    fprintf(stderr,
            "Usage: %s defaults | set-name <input|output> <name> | "
            "set-id <input|output> <device-id>\n", program);
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "defaults") == 0) {
        return print_default("input") && print_default("output") ? 0 : 2;
    }
    if (argc == 4 && strcmp(argv[1], "set-name") == 0) {
        AudioDeviceID device = kAudioObjectUnknown;
        if (default_selector(argv[2]) == 0 || !find_named(argv[2], argv[3], &device) ||
            !set_default(argv[2], device)) {
            fprintf(stderr, "Unable to set default %s device by name: %s\n",
                    argv[2], argv[3]);
            return 2;
        }
        return 0;
    }
    if (argc == 4 && strcmp(argv[1], "set-id") == 0) {
        AudioDeviceID device = kAudioObjectUnknown;
        if (default_selector(argv[2]) == 0 || !parsed_device_id(argv[3], &device) ||
            !set_default(argv[2], device)) {
            fprintf(stderr, "Unable to restore default %s device id: %s\n",
                    argv[2], argv[3]);
            return 2;
        }
        return 0;
    }
    usage(argv[0]);
    return 2;
}
