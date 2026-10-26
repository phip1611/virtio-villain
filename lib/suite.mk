# SPDX-License-Identifier: Apache-2.0
# Shared Make entry points for trusted external suite definitions.

VV_SUITE_BUILDER := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))/python/villain/build.py
VV_BUILD_OVERRIDES := $(foreach variable,$(.VARIABLES),$(if $(filter command line,$(origin $(variable))),$(variable)))
export VV_BUILD_OVERRIDES
ifneq ($(strip $(VV_BUILD_OVERRIDES)),)
export $(VV_BUILD_OVERRIDES)
endif
SUITE_BUILD_GOALS ?= $(filter init initramfs kernel prepare,$(MAKECMDGOALS))

.PHONY: init initramfs kernel prepare vv-suite-build
init initramfs kernel prepare: vv-suite-build

vv-suite-build:
	+@python3 "$(VV_SUITE_BUILDER)" --suite "$(SUITE)" $(if $(strip $(SUITE_BUILD_GOALS)),$(SUITE_BUILD_GOALS),initramfs)
