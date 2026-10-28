/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0106: Read with sector at capacity minus 1 but data length covers 2 sectors
 *
 * Spec 5.2.6.1: sector + data_len/512 must not exceed capacity.
 * Read at sector capacity-1 with 1024 bytes (2 sectors) of data.
 */
#include "tests/test.h"

static test_result_t test_blk_read_straddle_end(struct virtio_dev *dev,
                                                struct vring *vr)
{
    uint64_t capacity = 0;
    volatile uint8_t *cfg_space = dev->device_cfg;
    for (int i = 0; i < 8; i++)
        ((uint8_t *)&capacity)[i] = cfg_space[i];

    if (capacity < 2)
        return TEST_SKIP;

    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_IN;
    hdr->ioprio = 0;
    hdr->sector = capacity - 1; /* last valid sector */
    *status = 0xFF;

    /* Request 2 sectors (1024 bytes) starting at capacity-1 => exceeds by 1 */
    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 1024,
                       VRING_DESC_F_WRITE | VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0106, VIRTIO_PCI_DEVICE_BLK, test_blk_read_straddle_end,
              "Read 2 sectors starting at capacity-1 (straddles end)",
              VIRTIO_SPEC_V1_2, "5.2.6.1");
