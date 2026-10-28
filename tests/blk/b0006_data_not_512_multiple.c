/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0006: blk_data_not_512_multiple
 *
 * Submit a read request with data length = 513 (not a multiple of 512).
 */
#include "tests/test.h"
#include "lib/util.h"
#include "lib/vring.h"
#include "lib/virtio_pci.h"
#include "lib/virtio_spec.h"

#include <string.h>
#include <unistd.h>

static test_result_t test_blk_data_not_512(struct virtio_dev *dev,
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
    /* 513 bytes - not a sector multiple */
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 513,
                       VRING_DESC_F_NEXT | VRING_DESC_F_WRITE, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0006, VIRTIO_PCI_DEVICE_BLK, test_blk_data_not_512,
              "Data length not a multiple of 512",
              VIRTIO_SPEC_V1_2, "5.2.6");
