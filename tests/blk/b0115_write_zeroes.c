/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0115: blk_write_zeroes
 *
 * Submit a VIRTIO_BLK_T_WRITE_ZEROES request to zero a range of sectors.
 * Spec 5.2.6.2: The device MUST zero the specified range if the
 * VIRTIO_BLK_F_WRITE_ZEROES feature has been negotiated.
 */
#include "tests/test.h"
#include "lib/util.h"
#include "lib/vring.h"
#include "lib/virtio_pci.h"
#include "lib/virtio_spec.h"

#include <string.h>
#include <unistd.h>

static test_result_t test_blk_write_zeroes(struct virtio_dev *dev,
                                           struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    struct virtio_blk_discard_write_zeroes *seg = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_WRITE_ZEROES;
    hdr->ioprio = 0;
    hdr->sector = 0;

    seg->sector = 0;
    seg->num_sectors = 8;  /* 4KB */
    seg->flags = 0;

    *status = 0xFF;

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(seg), sizeof(*seg),
                       VRING_DESC_F_NEXT, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0115, VIRTIO_PCI_DEVICE_BLK, test_blk_write_zeroes,
              "Write zeroes to sector range",
              VIRTIO_SPEC_V1_2, "5.2.6.2");
