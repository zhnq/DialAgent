// Enumerate PJSIP audio IDs without opening a stream or a network transport.
#include <pjlib.h>
#include <pjmedia-audiodev/audiodev.h>
#include <stdio.h>
#include <unistd.h>

int main(void) {
    alarm(15);
    pj_log_set_level(0);
    if (pj_init() != PJ_SUCCESS) return 2;
    pj_caching_pool pool;
    pj_caching_pool_init(&pool, NULL, 0);
    if (pjmedia_aud_subsys_init(&pool.factory) != PJ_SUCCESS) return 2;
    for (unsigned i = 0; i < pjmedia_aud_dev_count(); ++i) {
        pjmedia_aud_dev_info info;
        if (pjmedia_aud_dev_get_info(i, &info) != PJ_SUCCESS) return 2;
        printf("DEVICE\t%u\t%s\t%u\t%u\n", i, info.name,
               info.input_count, info.output_count);
    }
    pjmedia_aud_subsys_shutdown();
    pj_caching_pool_destroy(&pool);
    pj_shutdown();
    return 0;
}
