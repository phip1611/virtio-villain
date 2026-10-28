/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0042: blk_write_sector_zero
 *
 * Write to sector 0 (the boot sector). Some VMM backends special-case
 * sector 0 for partition table handling or copy-on-write. This tests
 * whether writing to sector 0 triggers any assertion failures or
 * different code paths compared to other sectors.
 */
#include "tests/test.h"

static test_result_t test_blk_write_sector_zero(struct virtio_dev *dev,
                                                struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_OUT;
    hdr->ioprio = 0;
    hdr->sector = 0; /* boot sector */
    *status = 0xFF;

    /* Fill with a recognizable pattern */
    memset(data, 0xAA, 512);

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 512,
                       VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0042, VIRTIO_PCI_DEVICE_BLK, test_blk_write_sector_zero,
              "Write to sector 0 (boot sector)",
              VIRTIO_SPEC_V1_2, "5.2.6.1");
