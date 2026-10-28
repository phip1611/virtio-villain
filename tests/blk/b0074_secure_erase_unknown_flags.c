/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0074: Secure erase with unknown flags set
 *
 * Spec 5.2.6.2: "The device MUST set the status byte to
 * VIRTIO_BLK_S_UNSUPP for discard, secure erase and write zeroes
 * commands if any unknown flag is set."
 *
 * Submit SECURE_ERASE with flags=0x80000000 (bit 31, undefined).
 */
#include "tests/test.h"

static test_result_t test_blk_secure_erase_unknown_flags(struct virtio_dev *dev,
                                                         struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    struct virtio_blk_discard_write_zeroes *seg = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_SECURE_ERASE;
    hdr->sector = 0;

    seg->sector = 0;
    seg->num_sectors = 1;
    seg->flags = 0x80000000; /* unknown flag bit */

    *status = 0xFF;

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(seg), sizeof(*seg),
                       VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0074, VIRTIO_PCI_DEVICE_BLK, test_blk_secure_erase_unknown_flags,
              "Secure erase with unknown flag bit 31 set",
              VIRTIO_SPEC_V1_2, "5.2.6.2");
