/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0083: Discard overlapping segments in one request
 *
 * Submit a DISCARD request containing two segments that overlap
 * (same sector range). Tests that the device handles duplicate/
 * overlapping ranges without crash or data corruption.
 */
#include "tests/test.h"

static test_result_t test_blk_discard_overlap_segments(struct virtio_dev *dev,
                                                       struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    struct virtio_blk_discard_write_zeroes *segs = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_DISCARD;
    hdr->sector = 0;

    /* Two identical overlapping segments */
    segs[0].sector = 0;
    segs[0].num_sectors = 8;
    segs[0].flags = 0;
    segs[1].sector = 0;
    segs[1].num_sectors = 8;
    segs[1].flags = 0;

    *status = 0xFF;

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(segs), 2 * sizeof(segs[0]),
                       VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0083, VIRTIO_PCI_DEVICE_BLK, test_blk_discard_overlap_segments,
              "Discard with two identical overlapping segments",
              VIRTIO_SPEC_V1_2, "5.2.6");
