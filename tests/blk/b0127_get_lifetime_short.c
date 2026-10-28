/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0127: GET_LIFETIME with undersized response buffer.
 *
 * Submit a GET_LIFETIME request with a data buffer smaller than the
 * expected lifetime response structure.
 */
#include "tests/test.h"
#include "lib/util.h"
#include "lib/vring.h"
#include "lib/virtio_pci.h"
#include "lib/virtio_spec.h"

#include <string.h>

static test_result_t test_blk_get_lifetime_short(struct virtio_dev *dev,
                                                 struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_GET_LIFETIME;
    hdr->sector = 0;
    *status = 0xFF;

    /* Response needs at least 8 bytes, give only 2 */
    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 2,
                       VRING_DESC_F_WRITE | VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0127, VIRTIO_PCI_DEVICE_BLK, test_blk_get_lifetime_short,
              "GET_LIFETIME with undersized response buffer",
              VIRTIO_SPEC_V1_2, "5.2.6.3");
