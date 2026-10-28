/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0018: blk_write_zeroes_sector_overflow
 *
 * Submit a WRITE_ZEROES segment where sector + num_sectors wraps
 * around 64-bit arithmetic. The device must detect the overflow.
 *
 * Opcode 13 is VIRTIO_BLK_T_WRITE_ZEROES; DISCARD is opcode 11. The
 * discard handler already guards with a checked add, the write
 * zeroes path was the one missing the overflow check.
 */
#include "tests/test.h"

static test_result_t test_blk_write_zeroes_overflow(struct virtio_dev *dev,
                                                    struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    struct virtio_blk_discard_write_zeroes *seg = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_WRITE_ZEROES;
    hdr->ioprio = 0;
    hdr->sector = 0;
    *status = 0xFF;

    /* sector + num_sectors wraps 64-bit */
    seg->sector = 0xFFFFFFFFFFFFFF00ULL;
    seg->num_sectors = 0xFFFFFFFF;
    seg->flags = 0;

    uint64_t hdr_phys = vv_virt_to_phys(hdr);
    uint64_t seg_phys = vv_virt_to_phys(seg);
    uint64_t status_phys = vv_virt_to_phys(status);

    vring_raw_set_desc(vr, 0, hdr_phys, sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, seg_phys, sizeof(*seg),
                       VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, status_phys, 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0018, VIRTIO_PCI_DEVICE_BLK, test_blk_write_zeroes_overflow,
              "WRITE_ZEROES segment with sector + num_sectors overflow",
              VIRTIO_SPEC_V1_2, "5.2.6");
