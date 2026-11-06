/* SPDX-License-Identifier: GPL-2.0 */
/* NON-SHIPPING ONLY. Included after the real static __ceph_x_decrypt(). */
int cephx_sru_bounds_run(void);

int cephx_sru_bounds_run(void)
{
	/* Public synthetic legacy AES key: the bounds guard predates aes256k. */
	static const u8 synthetic_key[16] = {
		0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
	};
	struct ceph_crypto_key key = { .type = CEPH_CRYPTO_AES,
				       .len = sizeof(synthetic_key) };
	struct {
		u8 ciphertext[96];
		u8 decoded[96];
	} *buf;
	int len, plaintext_len, short_len, i, ret;

	/* Crypto consumes scatterlists; keep its buffers off the kernel stack. */
	buf = kzalloc(sizeof(*buf), GFP_KERNEL);
	if (!buf)
		return -ENOMEM;
	/* ceph_crypto_key_destroy() owns/frees key.key, so it must be a copy. */
	key.key = kmemdup(synthetic_key, sizeof(synthetic_key), GFP_KERNEL);
	if (!key.key) {
		ret = -ENOMEM;
		goto out;
	}
	ret = ceph_crypto_key_prepare(&key, NULL, 0);
	if (ret)
		goto out;

	/* First prove a normal CephX header + payload survives the real decrypt. */
	for (i = 0; i < 32; i++)
		buf->ciphertext[ceph_x_encrypt_offset(&key) + i] = i;
	len = ceph_x_encrypt(&key, 0, buf->ciphertext,
			     sizeof(buf->ciphertext), 32);
	if (len < 0) {
		ret = len;
		goto out;
	}
	/* The public encrypt helper prefixes a u32 length; the inner decrypt
	 * expects only ciphertext, with that framing word already stripped.
	 */
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
	pr_info("CEPHX_SRU07_CONTROL ciphertext=%d payload=32 result=32 PASS\n",
		len - (int)sizeof(u32));

	/* Bypass ceph_x_encrypt(), which always adds a complete header. Encrypt
	 * raw 0..8-byte plaintexts instead: valid AES padding, but shorter than
	 * the packed header (one version byte + eight magic bytes).
	 */
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
