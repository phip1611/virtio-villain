/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0164: virtio_blk WRITE with status descriptor placed before
 * the data buffer in the chain.
 *
 * Spec 5.2.6: the chain layout for a write is header, then
 * device readable data, then a one byte device writable status.
 * Reorder the writable status descriptor so it appears between
 * the header and the data buffer. The device must reject without
 * confusing status with data or scribbling on the data buffer.
 */
#include "tests/test.h"

static test_result_t test_blk_write_status_before_data(struct virtio_dev *dev,
                                                       struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_OUT;
    hdr->ioprio = 0;
    hdr->sector = 0;
    memset(data, 0x5A, 512);
    *status = 0xFF;

    /* Chain: header, status (writable), data (readable). Wrong order. */
    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE | VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(data), 512, 0, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0164, VIRTIO_PCI_DEVICE_BLK,
              test_blk_write_status_before_data,
              "Write chain with status descriptor before data buffer",
              VIRTIO_SPEC_V1_2, "5.2.6");
