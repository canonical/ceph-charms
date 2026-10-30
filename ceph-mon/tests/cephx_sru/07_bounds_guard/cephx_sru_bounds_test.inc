/* SPDX-License-Identifier: GPL-2.0 */
/* NON-SHIPPING ONLY. Included after the real static __ceph_x_decrypt(). */
int cephx_sru_bounds_run(void);

int cephx_sru_bounds_run(void)
{
	static const u8 synthetic_key[16] = {
		0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
	};
	struct ceph_crypto_key key = { .type = CEPH_CRYPTO_AES,
				       .len = sizeof(synthetic_key) };
	struct {
		u8 ciphertext[96];
		u8 decoded[96];
		u8 repeat[96];
	} *buf;
	int len, repeat_len, plaintext_len, short_len, i, ret;

	buf = kzalloc(sizeof(*buf), GFP_KERNEL);
	if (!buf)
		return -ENOMEM;
	key.key = kmemdup(synthetic_key, sizeof(synthetic_key), GFP_KERNEL);
	if (!key.key) {
		ret = -ENOMEM;
		goto out;
	}
	ret = ceph_crypto_key_prepare(&key, NULL, 0);
	if (ret)
		goto out;

	/* Fixed key + Ceph's fixed AES IV + fixed payload: identical ciphertext. */
	for (i = 0; i < 32; i++) {
		buf->ciphertext[ceph_x_encrypt_offset(&key) + i] = i;
		buf->repeat[ceph_x_encrypt_offset(&key) + i] = i;
	}
	len = ceph_x_encrypt(&key, 0, buf->ciphertext,
			     sizeof(buf->ciphertext), 32);
	if (len < 0) {
		ret = len;
		goto out;
	}
	repeat_len = ceph_x_encrypt(&key, 0, buf->repeat,
				    sizeof(buf->repeat), 32);
	if (repeat_len != len || memcmp(buf->ciphertext, buf->repeat, len)) {
		ret = -EUCLEAN;
		goto out;
	}
	ret = __ceph_x_decrypt(&key, 0, buf->ciphertext + sizeof(u32),
			       len - sizeof(u32));
	if (ret != 32) {
		ret = -EUCLEAN;
		goto out;
	}
	for (i = 0; i < 32; i++) {
		if (buf->ciphertext[ceph_x_encrypt_offset(&key) + i] != i) {
			ret = -EUCLEAN;
			goto out;
		}
	}
	pr_info("CEPHX_SRU07_CONTROL ciphertext=%d payload=32 deterministic=1 result=32 PASS\n",
		len - (int)sizeof(u32));

	/* Every sub-header plaintext length, including valid encrypted emptiness. */
	for (short_len = 0; short_len < sizeof(struct ceph_x_encrypt_header); short_len++) {
		memset(buf, 0, sizeof(*buf));
		for (i = 0; i < short_len; i++)
			buf->ciphertext[i] = i ^ 0x5a;
		ret = ceph_crypt(&key, 0, true, buf->ciphertext,
				 sizeof(buf->ciphertext), short_len, &len);
		if (ret)
			goto out;
		if (len != ceph_crypt_buflen(&key, short_len)) {
			ret = -EUCLEAN;
			goto out;
		}
		/* Prove padding/crypto succeeds, before testing the production guard. */
		memcpy(buf->decoded, buf->ciphertext, len);
		ret = ceph_crypt(&key, 0, false, buf->decoded, len, len, &plaintext_len);
		if (ret)
			goto out;
		if (plaintext_len != short_len) {
			ret = -EUCLEAN;
			goto out;
		}
		for (i = 0; i < short_len; i++) {
			if (buf->decoded[i] != (i ^ 0x5a)) {
				ret = -EUCLEAN;
				goto out;
			}
		}
		ret = __ceph_x_decrypt(&key, 0, buf->ciphertext, len);
		if (ret != -EINVAL) {
			pr_err("CEPHX_SRU07 unexpected decrypt result=%d\n", ret);
			ret = -EUCLEAN;
			goto out;
		}
		pr_info("CEPHX_SRU07_SHORT ciphertext=%d plaintext=%d header=%zu result=%d PASS\n",
			len, plaintext_len, sizeof(struct ceph_x_encrypt_header), ret);
	}
	ret = 0;
out:
	ceph_crypto_key_destroy(&key);
	kfree_sensitive(buf);
	return ret;
}
EXPORT_SYMBOL_GPL(cephx_sru_bounds_run);
