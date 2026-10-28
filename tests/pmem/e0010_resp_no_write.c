/* SPDX-License-Identifier: Apache-2.0 */
/*
 * E0010: Pmem flush response with no WRITE flag.
 *
 * Response descriptor lacks VRING_DESC_F_WRITE. Per spec 2.7.5.3
 * a device-writable buffer requires the flag; the device must
 * reject the malformed chain rather than write to a read-only
 * mapping.
 */
#include "tests/test.h"

static test_result_t test_pmem_resp_no_write(struct virtio_dev *dev,
                                             struct vring *vr)
{
    struct virtio_pmem_req *req = vv_alloc_pages(1);
    req->type = VIRTIO_PMEM_REQ_TYPE_FLUSH;
    void *r = vv_alloc_pages(1);

    vring_raw_set_desc(vr, 0, vv_virt_to_phys(req), sizeof(*req),
                       VRING_DESC_F_NEXT, 1);
    vring_raw_set_desc(vr, 1, vv_virt_to_phys(r), 4, 0, 0);
    return vv_submit_and_wait(dev, vr, 0);
}

REGISTER_TEST(E0010, VIRTIO_PCI_DEVICE_PMEM, test_pmem_resp_no_write,
              "Flush response without WRITE flag",
              VIRTIO_SPEC_V1_2, "2.7.5.3");
