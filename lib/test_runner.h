/* SPDX-License-Identifier: Apache-2.0 */
#ifndef VV_TEST_RUNNER_H
#define VV_TEST_RUNNER_H

#include <stddef.h>
#include "test.h"

struct vv_test_suite {
    const char *contract;
    const char *protocol_prefix;
    const struct vv_test_entry *begin;
    const struct vv_test_entry *end;
    size_t context_size;
    int (*preflight)(void);
    void (*init_context)(void *context);
    test_result_t (*cleanup)(void *context);
};

void vv_list_tests(const struct vv_test_suite *suite);
int vv_run_suite(const struct vv_test_suite *suite, const char *selection,
                 const char *run_id, int infrastructure_ready);
int vv_appliance_main(int argc, char **argv, const struct vv_test_suite *suite);

#endif
