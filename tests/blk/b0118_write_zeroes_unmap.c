/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0118: blk_discard_unmap_flag
 *
 * Submit WRITE_ZEROES with the unmap flag set (VIRTIO_BLK_WRITE_ZEROES_FLAG_UNMAP).
 * Spec 5.2.6.2: If the unmap flag is set, the device MAY additionally
 * deallocate the range after zeroing.
 */
#include "tests/test.h"

static test_result_t test_blk_discard_unmap_flag(struct virtio_dev *dev,
                                                 struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    struct virtio_blk_discard_write_zeroes *seg = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_WRITE_ZEROES;
    hdr->sector = 0;

    seg->sector = 0;
    seg->num_sectors = 16;  /* 8KB */
    seg->flags = VIRTIO_BLK_WRITE_ZEROES_FLAG_UNMAP;

    *status = 0xFF;

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(seg), sizeof(*seg),
                       VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0118, VIRTIO_PCI_DEVICE_BLK, test_blk_discard_unmap_flag,
              "Write zeroes with unmap flag",
              VIRTIO_SPEC_V1_2, "5.2.6.2");
