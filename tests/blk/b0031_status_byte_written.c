/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0031: blk_status_byte_written
 *
 * Submit a valid read request with the status byte pre-filled to 0xFF.
 * Verify the device actually writes the status byte (changes it from
 * 0xFF). A device that completes the used ring but never writes status
 * leaves the driver unable to determine success/failure.
 */
#include "tests/test.h"

static test_result_t test_blk_status_byte(struct virtio_dev *dev,
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
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 512,
                       VRING_DESC_F_NEXT | VRING_DESC_F_WRITE, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    test_result_t r = vv_submit_and_wait(dev, vr, 0);
    if (r != TEST_PASS)
        return r;

    /* Device completed - check if status was actually written */
    if (*status == 0xFF)
        TFAIL("*status == 0xFF");

    return TEST_PASS;
}

REGISTER_TEST(B0031, VIRTIO_PCI_DEVICE_BLK, test_blk_status_byte,
              "Verify device writes status byte on completion",
              VIRTIO_SPEC_V1_2, "5.2.6");
