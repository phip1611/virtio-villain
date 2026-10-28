/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0126: Data descriptor with NULL (zero) physical address.
 *
 * Submit a read with data descriptor address set to 0. The device
 * must not write to physical address zero.
 */
#include "tests/test.h"

static test_result_t test_blk_data_addr_zero(struct virtio_dev *dev,
                                             struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_IN;
    hdr->ioprio = 0;
    hdr->sector = 0;
    *status = 0xFF;

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    /* Data descriptor with address = 0 */
    vring_raw_set_desc(vr, 1, 0, 512,
                       VRING_DESC_F_WRITE | VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0126, VIRTIO_PCI_DEVICE_BLK, test_blk_data_addr_zero,
              "Data descriptor with zero physical address",
              VIRTIO_SPEC_V1_2, "5.2.6");
