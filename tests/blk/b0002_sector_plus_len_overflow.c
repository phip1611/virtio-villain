/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0002: blk_sector_plus_len_overflow
 *
 * Submit a read where sector * 512 + data_len wraps around 64-bit.
 */
#include "tests/test.h"

static test_result_t test_blk_sector_plus_len_overflow(struct virtio_dev *dev,
                                                       struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    /*
     * sector near max: sector * 512 is close to 2^64.
     * Adding data length wraps around.
     */
    hdr->type = VIRTIO_BLK_T_IN;
    hdr->ioprio = 0;
    hdr->sector = 0xFFFFFFFFFFFFFFFEULL;
    *status = 0xFF;

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 4096,
                       VRING_DESC_F_NEXT | VRING_DESC_F_WRITE, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0002, VIRTIO_PCI_DEVICE_BLK, test_blk_sector_plus_len_overflow,
              "sector * 512 + data_len wraps 64-bit",
              VIRTIO_SPEC_V1_2, "5.2.6");
