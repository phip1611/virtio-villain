/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0011: blk_header_too_short
 *
 * Submit a request where the header descriptor has len < 16 bytes
 * (sizeof virtio_blk_outhdr). The device cannot parse a truncated header.
 */
#include "tests/test.h"

static test_result_t test_blk_header_too_short(struct virtio_dev *dev,
                                               struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_IN;
    hdr->sector = 0;
    *status = 0xFF;

    /* Header descriptor with only 4 bytes (needs 16) */
    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), 4,
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 512,
                       VRING_DESC_F_NEXT | VRING_DESC_F_WRITE, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0011, VIRTIO_PCI_DEVICE_BLK, test_blk_header_too_short,
              "Header descriptor shorter than 16 bytes",
              VIRTIO_SPEC_V1_2, "5.2.6");
