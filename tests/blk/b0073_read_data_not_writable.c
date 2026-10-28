/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0073: Read request with data descriptor NOT writable
 *
 * For VIRTIO_BLK_T_IN (read), the data descriptor must be
 * device-writable. Submit a READ where the data lacks WRITE flag.
 */
#include "tests/test.h"

static test_result_t test_blk_read_data_not_writable(struct virtio_dev *dev,
                                                     struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_IN;
    hdr->sector = 0;
    *status = 0xFF;

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    /* Data NOT writable — wrong for a read request */
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 512,
                       VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0073, VIRTIO_PCI_DEVICE_BLK, test_blk_read_data_not_writable,
              "Read request with data descriptor not device-writable",
              VIRTIO_SPEC_V1_2, "5.2.6");
