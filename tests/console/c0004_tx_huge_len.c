/* SPDX-License-Identifier: Apache-2.0 */
/*
 * C0004: Console transmit with oversized length.
 *
 * Submit a transmitq descriptor whose length field claims a multi-MB
 * payload while the underlying allocation is only one page. The
 * device must validate the length against guest memory bounds and
 * must not read past the end of the buffer.
 */
#include "tests/test.h"
#include "lib/util.h"
#include "lib/vring.h"
#include "lib/virtio_pci.h"
#include "lib/virtio_spec.h"

#include <string.h>
#include <unistd.h>

static test_result_t test_console_tx_huge_len(struct virtio_dev *dev,
                                              struct vring *vr)
{
    uint8_t *buf = vv_alloc_pages(1);

    /* Length 1 GiB while only 4 KiB is mapped */
    vring_raw_set_desc(vr, 0, vv_virt_to_phys(buf), 0x40000000U, 0, 0);
    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST_Q(C0004, VIRTIO_PCI_DEVICE_CONSOLE, test_console_tx_huge_len,
                "Console transmit with 1 GiB length",
                VIRTIO_SPEC_V1_2, "5.3", 1);
