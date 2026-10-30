// SPDX-License-Identifier: GPL-2.0
/* NON-SHIPPING: explicitly run the kernel cryptomgr's RFC8009 AEAD vectors. */
#include <crypto/aead.h>
#include <linux/crypto.h>
#include <linux/err.h>
#include <linux/module.h>

/* GPL-exported by crypto/testmgr.c; not part of the public allocation API. */
extern int alg_test(const char *driver, const char *alg, u32 type, u32 mask);

static int __init cephx_sru_crypto_init(void)
{
	static const char * const algorithms[] = {
		"authenc(hmac(sha256),cts(cbc(aes)))",
		"authenc(hmac(sha384),cts(cbc(aes)))",
	};
	struct crypto_aead *tfm;
	int i, ret;

	BUILD_BUG_ON(IS_ENABLED(CONFIG_CRYPTO_MANAGER_DISABLE_TESTS));
	BUILD_BUG_ON(!IS_ENABLED(CONFIG_CRYPTO_MANAGER_EXTRA_TESTS));
	BUILD_BUG_ON(!IS_ENABLED(CONFIG_CRYPTO_KRB5_SELFTESTS));
	for (i = 0; i < ARRAY_SIZE(algorithms); i++) {
		tfm = crypto_alloc_aead(algorithms[i], 0, 0);
		if (IS_ERR(tfm))
			return PTR_ERR(tfm);
		/* Both names map to nonempty RFC8009 vector suites, not alg_test_null. */
		ret = alg_test(crypto_aead_driver_name(tfm), algorithms[i],
			       CRYPTO_ALG_TYPE_AEAD, CRYPTO_ALG_TYPE_MASK);
		if (!ret)
			pr_info("CEPHX_SRU_CRYPTO vectors=%s driver=%s result=0 PASS\n",
				algorithms[i], crypto_aead_driver_name(tfm));
		crypto_free_aead(tfm);
		if (ret)
			return ret;
	}
	return 0;
}

static void __exit cephx_sru_crypto_exit(void) {}
module_init(cephx_sru_crypto_init);
module_exit(cephx_sru_crypto_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("NON-SHIPPING CephX crypto vector evidence");
