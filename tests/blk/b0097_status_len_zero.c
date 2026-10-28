/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0097: Status descriptor with length 0 (must be at least 1 byte)
 *
 * Spec 5.2.6: The final status byte is device-writable, 1 byte.
 * Provide a status descriptor with length 0.
 */
#include "tests/test.h"
#include "lib/util.h"
#include "lib/vring.h"
#include "lib/virtio_pci.h"
#include "lib/virtio_spec.h"

#include <string.h>

static test_result_t test_blk_status_len_zero(struct virtio_dev *dev,
                                              struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_IN;
    hdr->ioprio = 0;
    hdr->sector = 0;
    *status = 0xFF;

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 512,
                       VRING_DESC_F_WRITE | VRING_DESC_F_NEXT, 2);
    /* Status descriptor with length 0 */
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 0,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0097, VIRTIO_PCI_DEVICE_BLK, test_blk_status_len_zero,
              "Status descriptor with length 0",
              VIRTIO_SPEC_V1_2, "5.2.6");
