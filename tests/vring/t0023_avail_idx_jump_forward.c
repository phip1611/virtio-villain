/* SPDX-License-Identifier: Apache-2.0 */
/*
 * T0023: avail_idx_jump_forward
 *
 * Advance avail.idx by queue_size * 2 in a single step. The VMM
 * calculates the number of new entries as (avail.idx - last_seen_idx)
 * which becomes a huge number. If it then loops through all those
 * "entries" (most of which are uninitialized ring slots), it may:
 * - Read garbage descriptor indices from uninitialized ring memory
 * - Spend excessive CPU time processing phantom requests
 * - Index out of bounds in the descriptor table
 *
 * Correct behavior: process only the valid entries (the ones between
 * old avail.idx and new avail.idx that fit within the ring).
 */
#include "tests/test.h"
#include "lib/util.h"
#include "lib/vring.h"
#include "lib/virtio_pci.h"
#include "lib/virtio_spec.h"

#include <string.h>
#include <unistd.h>

static test_result_t test_avail_idx_jump_forward(struct virtio_dev *dev,
                                                 struct vring *vr)
{
    struct virtio_blk_outhdr *hdr = vv_alloc_pages(1);
    uint8_t *data = vv_alloc_pages(1);
    uint8_t *status = vv_alloc_pages(1);

    hdr->type = VIRTIO_BLK_T_IN;
    hdr->sector = 0;
    *status = 0xFF;

    uint16_t qsz = vr->size;

    /*
     * Set up one valid request at ring slot 0.
     */
    vring_raw_set_desc(vr, 0, vv_virt_to_phys(hdr), sizeof(*hdr),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(data), 512,
                       VRING_DESC_F_NEXT | VRING_DESC_F_WRITE, 2);
    vring_raw_set_desc(vr, 2, vv_virt_to_phys(status), 1,
                       VRING_DESC_F_WRITE, 0);

    vring_raw_set_avail(vr, 0, 0);

    /*
     * Jump avail.idx forward by 2 * queue_size. The VMM sees a delta
     * of (2*qsz) new entries, but only ring slot 0 has a valid head.
     * The rest are uninitialized (zero or garbage).
     */
    vring_raw_set_avail_idx(vr, qsz * 2);

    return vv_kick_and_wait(dev, vr, 0, VV_TIMEOUT_MS);
}

REGISTER_TEST(T0023, VIRTIO_PCI_DEVICE_BLK, test_avail_idx_jump_forward,
              "Available idx jumps forward by 2x queue size",
              VIRTIO_SPEC_V1_2, "2.7.6");
