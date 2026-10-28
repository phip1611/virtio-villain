/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0109: Header marked as device-writable
 *
 * Spec 5.2.6: The header is driver-readable (device-readable).
 * Mark it with WRITE flag (device-writable) - wrong direction.
 */
#include "tests/test.h"

static test_result_t test_blk_header_device_writable(struct virtio_dev *dev,
                                                     struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_IN;
    hdr->ioprio = 0;
    hdr->sector = 0;
    *status = 0xFF;

    uint64_t hdr_phys = vv_virt_to_phys(hdr);
    uint64_t data_phys = vv_virt_to_phys(data);
    uint64_t status_phys = vv_virt_to_phys(status);

    /* Header with WRITE flag (wrong - should be readable) */
    vring_raw_set_desc(vr, 0, hdr_phys, sizeof(*hdr),
                       VRING_DESC_F_WRITE | VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, data_phys, 512,
                       VRING_DESC_F_WRITE | VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, status_phys, 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0109, VIRTIO_PCI_DEVICE_BLK, test_blk_header_device_writable,
              "Header descriptor marked device-writable (wrong direction)",
              VIRTIO_SPEC_V1_2, "5.2.6");
