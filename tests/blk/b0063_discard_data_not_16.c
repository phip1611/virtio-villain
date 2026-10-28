/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0063: Discard data length not a multiple of 16
 *
 * Spec 5.2.6.1: "The length of data MUST be a multiple of the size
 * of struct virtio_blk_discard_write_zeroes" (16 bytes).
 *
 * Submit DISCARD with data length = 17 (not a multiple of 16).
 */
#include "tests/test.h"
#include "lib/util.h"
#include "lib/vring.h"
#include "lib/virtio_pci.h"
#include "lib/virtio_spec.h"

#include <string.h>

static test_result_t test_blk_discard_bad_data_len(struct virtio_dev *dev,
                                                   struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_DISCARD;
    hdr->sector = 0;
    *status = 0xFF;

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    /* 17 bytes — not a multiple of 16 */
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 17,
                       VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0063, VIRTIO_PCI_DEVICE_BLK, test_blk_discard_bad_data_len,
              "Discard data length not a multiple of 16 bytes",
              VIRTIO_SPEC_V1_2, "5.2.6.1");
