// SPDX-License-Identifier: GPL-2.0
/* NON-SHIPPING: never replace the production decrypt or force its return. */
#include <linux/module.h>

extern int cephx_sru_bounds_run(void);

static int __init cephx_sru_bounds_init(void)
{
	int ret = cephx_sru_bounds_run();

	if (!ret)
		pr_info("CEPHX_SRU07_COMPLETE PASS\n");
	return ret;
}

static void __exit cephx_sru_bounds_exit(void) {}
module_init(cephx_sru_bounds_init);
module_exit(cephx_sru_bounds_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("NON-SHIPPING actual CephX decrypt bounds validation");
