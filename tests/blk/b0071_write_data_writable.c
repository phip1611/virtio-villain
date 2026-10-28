/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0071: Write request with data descriptor marked writable
 *
 * For VIRTIO_BLK_T_OUT (write), the data must be device-readable.
 * Submit a write where the data descriptor has VRING_DESC_F_WRITE
 * (device-writable), so the device cannot read the write payload.
 */
#include "tests/test.h"
#include "lib/util.h"
#include "lib/vring.h"
#include "lib/virtio_pci.h"
#include "lib/virtio_spec.h"

#include <string.h>

static test_result_t test_blk_write_data_writable(struct virtio_dev *dev,
                                                  struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_OUT;
    hdr->sector = 0;
    memset(data, 0x55, 512);
    *status = 0xFF;

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    /* Data marked WRITE — wrong for a write request */
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 512,
                       VRING_DESC_F_NEXT | VRING_DESC_F_WRITE, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0071, VIRTIO_PCI_DEVICE_BLK, test_blk_write_data_writable,
              "Write request with data descriptor marked device-writable",
              VIRTIO_SPEC_V1_2, "5.2.6");
