/* SPDX-License-Identifier: Apache-2.0 */
/*
 * L0018: Balloon inflate empty buffer (len=0).
 *
 * Submit a zero-length descriptor: a buffer holding no PFNs. The
 * device must consume it as a no-op without crashing.
 *
 * Spec 5.5.6.1.
 */
#include "tests/test.h"

static test_result_t test_balloon_zero_len(struct virtio_dev *dev,
                                           struct vring *vr)
{
    uint32_t *pfns = vv_alloc_pages(1);
    uint64_t p = vv_virt_to_phys(pfns);

    vring_raw_set_desc(vr, 0, p, 0, 0, 0);
    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(L0018, VIRTIO_PCI_DEVICE_BALLOON, test_balloon_zero_len,
              "Inflate zero length",
              VIRTIO_SPEC_V1_2, "5.5.6.1");
