/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0098: Read request with two data descriptors (scattered read)
 *
 * Submit a READ with data split across two 256-byte writable
 * descriptors (total 512 bytes). Tests scatter-gather handling.
 */
#include "tests/test.h"

static test_result_t test_blk_read_scattered_data(struct virtio_dev *dev,
                                                  struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data1 = vv_alloc_pages(1);
    uint8_t *data2 = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_IN;
    hdr->ioprio = 0;
    hdr->sector = 0;
    *status = 0xFF;

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data1), 256,
                       VRING_DESC_F_WRITE | VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(data2), 256,
                       VRING_DESC_F_WRITE | VRING_DESC_F_NEXT, 3);
    vring_raw_set_desc(vr, 3, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0098, VIRTIO_PCI_DEVICE_BLK, test_blk_read_scattered_data,
              "Read with data split across two 256-byte descriptors",
              VIRTIO_SPEC_V1_2, "5.2.6");
