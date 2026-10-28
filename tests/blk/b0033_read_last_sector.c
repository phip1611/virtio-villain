/* SPDX-License-Identifier: Apache-2.0 */
/*
 * B0033: blk_read_last_sector
 *
 * Read the very last sector of the disk (capacity - 1). This is a
 * boundary test: the request is valid but any off-by-one error in the
 * VMM's capacity check would reject it.
 */
#include "tests/test.h"

static test_result_t test_blk_read_last_sector(struct virtio_dev *dev,
                                               struct vring *vr)
{
    /*
     * The test harness creates a 16 MiB disk = 32768 sectors.
     * The last valid sector index is 32767.
     */
    uint64_t last_sector = 32767;

    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_IN;
    hdr->ioprio = 0;
    hdr->sector = last_sector; /* last valid sector */
    *status = 0xFF;

    uint64_t hdr_phys = vv_virt_to_phys(hdr);
    uint64_t data_phys = vv_virt_to_phys(data);
    uint64_t status_phys = vv_virt_to_phys(status);

    vring_raw_set_desc(vr, 0, hdr_phys, sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, data_phys, 512,
                       VRING_DESC_F_NEXT | VRING_DESC_F_WRITE, 2);
    vring_raw_set_desc(vr, 2, status_phys, 1,
                       VRING_DESC_F_WRITE, 0);

    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(B0033, VIRTIO_PCI_DEVICE_BLK, test_blk_read_last_sector,
              "Read exactly the last sector (capacity - 1)",
              VIRTIO_SPEC_V1_2, "5.2.6");
