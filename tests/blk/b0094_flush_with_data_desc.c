/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0094: Flush with nonzero data length in descriptor
 *
 * Spec 5.2.6.1: FLUSH requests should have no data segment.
 * Provide a 512-byte readable data descriptor between header and status.
 */
#include "tests/test.h"

static test_result_t test_blk_flush_with_data_desc(struct virtio_dev *dev,
                                                   struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_FLUSH;
    hdr->sector = 0;
    memset(data, 0xBB, 512);
    *status = 0xFF;

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 512,
                       VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0094, VIRTIO_PCI_DEVICE_BLK, test_blk_flush_with_data_desc,
              "Flush with extraneous 512-byte data descriptor",
              VIRTIO_SPEC_V1_2, "5.2.6.1");
