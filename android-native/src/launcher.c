/* Native home screen, stage 0: fills the screen and shows that the native pipeline works. */
#include <android/log.h>
#include <android/native_activity.h>
#include <android/native_window.h>
#include <stdint.h>
#include <string.h>

#define LOG_TAG "AliceNative"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)

static void fill(ANativeWindow* window, uint32_t argb) {
    ANativeWindow_Buffer buf;
    if (ANativeWindow_lock(window, &buf, NULL) != 0) return;
    uint32_t* px = (uint32_t*)buf.bits;
    for (int32_t y = 0; y < buf.height; y++) {
        for (int32_t x = 0; x < buf.width; x++) px[x] = argb;
        px += buf.stride;
    }
    ANativeWindow_unlockAndPost(window);
}

static void on_window_created(ANativeActivity* activity, ANativeWindow* window) {
    (void)activity;
    LOGI("window created");
    fill(window, 0xFF101418u);
}

static void on_destroy(ANativeActivity* activity) {
    (void)activity;
    LOGI("destroy");
}

void ANativeActivity_onCreate(ANativeActivity* activity, void* saved, size_t size) {
    (void)saved;
    (void)size;
    activity->callbacks->onNativeWindowCreated = on_window_created;
    activity->callbacks->onDestroy = on_destroy;
    LOGI("native launcher start");
}
