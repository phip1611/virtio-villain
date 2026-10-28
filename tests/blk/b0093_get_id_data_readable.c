/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0093: GET_ID with data descriptor marked as readable (wrong direction)
 *
 * Spec 5.2.6: For VIRTIO_BLK_T_GET_ID, the data buffer should be
 * device-writable so the device can write the serial. Mark it readable.
 */
#include "tests/test.h"
#include "lib/util.h"
#include "lib/vring.h"
#include "lib/virtio_pci.h"
#include "lib/virtio_spec.h"

#include <string.h>

static test_result_t test_blk_get_id_data_readable(struct virtio_dev *dev,
                                                   struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_GET_ID;
    hdr->sector = 0;
    *status = 0xFF;

    /* Data descriptor is readable (no WRITE flag) - wrong for GET_ID */
    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 20,
                       VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0093, VIRTIO_PCI_DEVICE_BLK, test_blk_get_id_data_readable,
              "GET_ID with data descriptor marked readable (wrong direction)",
              VIRTIO_SPEC_V1_2, "5.2.6");
