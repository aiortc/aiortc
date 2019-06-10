#include <Python.h>
#include <openssl/evp.h>

#define MODULE_NAME "aioquic._crypto"

#define AEAD_KEY_LENGTH_MAX 32
#define AEAD_NONCE_LENGTH 12
#define AEAD_TAG_LENGTH 16

#define PACKET_LENGTH_MAX 1500
#define PACKET_NUMBER_LENGTH_MAX 4
#define SAMPLE_LENGTH 16

#define CHECK_RESULT(expr) \
    if (!expr) { \
        PyErr_SetString(PyExc_Exception, "OpenSSL call failed"); \
        return NULL; \
    }

#define CHECK_RESULT_CTOR(expr) \
    if (!expr) { \
        PyErr_SetString(PyExc_Exception, "OpenSSL call failed"); \
        return -1; \
    }

static PyObject *CryptoError;

/* AEAD */

typedef struct {
    PyObject_HEAD
    EVP_CIPHER_CTX *decrypt_ctx;
    EVP_CIPHER_CTX *encrypt_ctx;
    unsigned char buffer[PACKET_LENGTH_MAX];
    unsigned char key[AEAD_KEY_LENGTH_MAX];
    unsigned char nonce[AEAD_NONCE_LENGTH];
} AEADObject;

static EVP_CIPHER_CTX *
create_ctx(const EVP_CIPHER *cipher, int key_length, int operation)
{
    EVP_CIPHER_CTX *ctx;
    int res;

    ctx = EVP_CIPHER_CTX_new();
    CHECK_RESULT(ctx != 0);

    res = EVP_CipherInit_ex(ctx, cipher, NULL, NULL, NULL, operation);
    CHECK_RESULT(res != 0);

    res = EVP_CIPHER_CTX_set_key_length(ctx, key_length);
    CHECK_RESULT(res != 0);

    res = EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_CCM_SET_IVLEN, AEAD_NONCE_LENGTH, NULL);
    CHECK_RESULT(res != 0);

    return ctx;
}

static int
AEAD_init(AEADObject *self, PyObject *args, PyObject *kwargs)
{
    const char *cipher_name;
    const unsigned char *key;
    int cipher_name_len, key_len;

    if (!PyArg_ParseTuple(args, "y#y#", &cipher_name, &cipher_name_len, &key, &key_len))
        return -1;
    assert(key_len <= AEAD_KEY_LENGTH_MAX);
    memcpy(self->key, key, key_len);

    const EVP_CIPHER *evp_cipher = EVP_get_cipherbyname(cipher_name);
    CHECK_RESULT_CTOR(evp_cipher != 0);

    self->decrypt_ctx = create_ctx(evp_cipher, key_len, 0);
    if (self->decrypt_ctx == 0) return -1;

    self->encrypt_ctx = create_ctx(evp_cipher, key_len, 1);
    if (self->decrypt_ctx == 0) return -1;

    return 0;
}

static void
AEAD_dealloc(AEADObject *self)
{
    EVP_CIPHER_CTX_free(self->decrypt_ctx);
    EVP_CIPHER_CTX_free(self->encrypt_ctx);
}

static PyObject*
AEAD_decrypt(AEADObject *self, PyObject *args)
{
    const unsigned char *iv, *data, *associated;
    int pn_len, iv_len, data_len, associated_len, outlen, outlen2, res;

    if (!PyArg_ParseTuple(args, "y#y#y#", &iv, &iv_len, &data, &data_len, &associated, &associated_len))
        return NULL;

    assert(iv_len >= AEAD_NONCE_LENGTH);
    assert(data_len >= AEAD_TAG_LENGTH);

    pn_len = (associated[0] & 0x03) + 1;
    memcpy(self->nonce, iv, AEAD_NONCE_LENGTH);
    for (int i = 1; i <= pn_len; ++i) {
        self->nonce[AEAD_NONCE_LENGTH - i] ^= associated[associated_len - i];
    }

    res = EVP_CIPHER_CTX_ctrl(self->decrypt_ctx, EVP_CTRL_CCM_SET_TAG, AEAD_TAG_LENGTH, (void*)data + (data_len - AEAD_TAG_LENGTH));
    CHECK_RESULT(res != 0);

    res = EVP_CipherInit_ex(self->decrypt_ctx, NULL, NULL, self->key, self->nonce, 0);
    CHECK_RESULT(res != 0);

    res = EVP_CipherUpdate(self->decrypt_ctx, NULL, &outlen, associated, associated_len);
    CHECK_RESULT(res != 0);

    res = EVP_CipherUpdate(self->decrypt_ctx, self->buffer, &outlen, data, data_len - AEAD_TAG_LENGTH);
    CHECK_RESULT(res != 0);

    res = EVP_CipherFinal_ex(self->decrypt_ctx, NULL, &outlen2);
    if (res == 0) {
        PyErr_SetString(CryptoError, "Payload decryption failed");
        return NULL;
    }

    return PyBytes_FromStringAndSize((const char*)self->buffer, outlen);
}

static PyObject*
AEAD_encrypt(AEADObject *self, PyObject *args)
{
    const unsigned char *iv, *data, *associated;
    int pn_len, iv_len, data_len, associated_len, outlen, outlen2, res;

    if (!PyArg_ParseTuple(args, "y#y#y#", &iv, &iv_len, &data, &data_len, &associated, &associated_len))
        return NULL;

    assert(iv_len >= AEAD_NONCE_LENGTH);

    pn_len = (associated[0] & 0x03) + 1;
    memcpy(self->nonce, iv, AEAD_NONCE_LENGTH);
    for (int i = 1; i <= pn_len; ++i) {
        self->nonce[AEAD_NONCE_LENGTH - i] ^= associated[associated_len - i];
    }

    res = EVP_CipherInit_ex(self->encrypt_ctx, NULL, NULL, self->key, self->nonce, 1);
    CHECK_RESULT(res != 0);

    res = EVP_CipherUpdate(self->encrypt_ctx, NULL, &outlen, associated, associated_len);
    CHECK_RESULT(res != 0);

    res = EVP_CipherUpdate(self->encrypt_ctx, self->buffer, &outlen, data, data_len);
    CHECK_RESULT(res != 0);

    res = EVP_CipherFinal_ex(self->encrypt_ctx, NULL, &outlen2);
    CHECK_RESULT(res != 0 && outlen2 == 0);

    res = EVP_CIPHER_CTX_ctrl(self->encrypt_ctx, EVP_CTRL_CCM_GET_TAG, AEAD_TAG_LENGTH, self->buffer + outlen);
    CHECK_RESULT(res != 0);

    return PyBytes_FromStringAndSize((const char*)self->buffer, outlen + AEAD_TAG_LENGTH);
}

static PyMethodDef AEAD_methods[] = {
    {"decrypt", (PyCFunction)AEAD_decrypt, METH_VARARGS, ""},
    {"encrypt", (PyCFunction)AEAD_encrypt, METH_VARARGS, ""},

    {NULL}
};

static PyTypeObject AEADType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    MODULE_NAME ".AEAD",                /* tp_name */
    sizeof(AEADObject),                 /* tp_basicsize */
    0,                                  /* tp_itemsize */
    (destructor)AEAD_dealloc,           /* tp_dealloc */
    0,                                  /* tp_print */
    0,                                  /* tp_getattr */
    0,                                  /* tp_setattr */
    0,                                  /* tp_reserved */
    0,                                  /* tp_repr */
    0,                                  /* tp_as_number */
    0,                                  /* tp_as_sequence */
    0,                                  /* tp_as_mapping */
    0,                                  /* tp_hash  */
    0,                                  /* tp_call */
    0,                                  /* tp_str */
    0,                                  /* tp_getattro */
    0,                                  /* tp_setattro */
    0,                                  /* tp_as_buffer */
    Py_TPFLAGS_DEFAULT,                 /* tp_flags */
    "AEAD objects",                     /* tp_doc */
    0,                                  /* tp_traverse */
    0,                                  /* tp_clear */
    0,                                  /* tp_richcompare */
    0,                                  /* tp_weaklistoffset */
    0,                                  /* tp_iter */
    0,                                  /* tp_iternext */
    AEAD_methods,                       /* tp_methods */
    0,                                  /* tp_members */
    0,                                  /* tp_getset */
    0,                                  /* tp_base */
    0,                                  /* tp_dict */
    0,                                  /* tp_descr_get */
    0,                                  /* tp_descr_set */
    0,                                  /* tp_dictoffset */
    (initproc)AEAD_init,                /* tp_init */
    0,                                  /* tp_alloc */
};

/* HeaderProtection */

typedef struct {
    PyObject_HEAD
    EVP_CIPHER_CTX *ctx;
    int is_chacha20;
    unsigned char buffer[PACKET_LENGTH_MAX];
    unsigned char mask[31];
    unsigned char zero[5];
} HeaderProtectionObject;

static int
HeaderProtection_init(HeaderProtectionObject *self, PyObject *args, PyObject *kwargs)
{
    const char *cipher_name;
    const unsigned char *key;
    int cipher_name_len, key_len, res;

    if (!PyArg_ParseTuple(args, "y#y#", &cipher_name, &cipher_name_len, &key, &key_len))
        return -1;

    memset(self->mask, 0, sizeof(self->mask));
    memset(self->zero, 0, sizeof(self->zero));
    self->is_chacha20 = cipher_name_len == 8 && memcmp(cipher_name, "chacha20", 8) == 0;

    const EVP_CIPHER *evp_cipher = EVP_get_cipherbyname(cipher_name);
    CHECK_RESULT_CTOR(evp_cipher != 0);

    self->ctx = EVP_CIPHER_CTX_new();
    CHECK_RESULT_CTOR(self->ctx != 0);

    res = EVP_CipherInit_ex(self->ctx, evp_cipher, NULL, NULL, NULL, 1);
    CHECK_RESULT_CTOR(res != 0);

    res = EVP_CIPHER_CTX_set_key_length(self->ctx, key_len);
    CHECK_RESULT_CTOR(res != 0);

    res = EVP_CipherInit_ex(self->ctx, NULL, NULL, key, NULL, 1);
    CHECK_RESULT_CTOR(res != 0);

    return 0;
}

static void
HeaderProtection_dealloc(HeaderProtectionObject *self)
{
    EVP_CIPHER_CTX_free(self->ctx);
}

static int HeaderProtection_mask(HeaderProtectionObject *self, const unsigned char* sample)
{
    int outlen;
    if (self->is_chacha20) {
        return EVP_CipherInit_ex(self->ctx, NULL, NULL, NULL, sample, 1) &&
               EVP_CipherUpdate(self->ctx, self->mask, &outlen, self->zero, sizeof(self->zero));
    } else {
        return EVP_CipherUpdate(self->ctx, self->mask, &outlen, sample, SAMPLE_LENGTH);
    }
}

static PyObject*
HeaderProtection_apply(HeaderProtectionObject *self, PyObject *args)
{
    const unsigned char *header, *payload;
    int header_len, payload_len, res;

    if (!PyArg_ParseTuple(args, "y#y#", &header, &header_len, &payload, &payload_len))
        return NULL;

    int pn_length = (header[0] & 0x03) + 1;
    int pn_offset = header_len - pn_length;

    res = HeaderProtection_mask(self, payload + PACKET_NUMBER_LENGTH_MAX - pn_length);
    CHECK_RESULT(res != 0);

    memcpy(self->buffer, header, header_len);
    memcpy(self->buffer + header_len, payload, payload_len);

    if (self->buffer[0] & 0x80) {
        self->buffer[0] ^= self->mask[0] & 0x0F;
    } else {
        self->buffer[0] ^= self->mask[0] & 0x1F;
    }

    for (int i = 0; i < pn_length; ++i) {
        self->buffer[pn_offset + i] ^= self->mask[1 + i];
    }

    return PyBytes_FromStringAndSize((const char*)self->buffer, header_len + payload_len);
}

static PyObject*
HeaderProtection_remove(HeaderProtectionObject *self, PyObject *args)
{
    const unsigned char *packet;
    int pn_offset, packet_len, res;

    if (!PyArg_ParseTuple(args, "y#I", &packet, &packet_len, &pn_offset))
        return NULL;

    res = HeaderProtection_mask(self, packet + pn_offset + PACKET_NUMBER_LENGTH_MAX);
    CHECK_RESULT(res != 0);

    memcpy(self->buffer, packet, pn_offset + PACKET_NUMBER_LENGTH_MAX);

    if (self->buffer[0] & 0x80) {
        self->buffer[0] ^= self->mask[0] & 0x0F;
    } else {
        self->buffer[0] ^= self->mask[0] & 0x1F;
    }

    int pn_length = (self->buffer[0] & 0x03) + 1;
    for (int i = 0; i < pn_length; ++i) {
        self->buffer[pn_offset + i] ^= self->mask[1 + i];
    }

    return PyBytes_FromStringAndSize((const char*)self->buffer, pn_offset + pn_length);
}

static PyMethodDef HeaderProtection_methods[] = {
    {"apply", (PyCFunction)HeaderProtection_apply, METH_VARARGS, ""},
    {"remove", (PyCFunction)HeaderProtection_remove, METH_VARARGS, ""},
    {NULL}
};

static PyTypeObject HeaderProtectionType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    MODULE_NAME ".HeaderProtection",    /* tp_name */
    sizeof(HeaderProtectionObject),     /* tp_basicsize */
    0,                                  /* tp_itemsize */
    (destructor)HeaderProtection_dealloc,   /* tp_dealloc */
    0,                                  /* tp_print */
    0,                                  /* tp_getattr */
    0,                                  /* tp_setattr */
    0,                                  /* tp_reserved */
    0,                                  /* tp_repr */
    0,                                  /* tp_as_number */
    0,                                  /* tp_as_sequence */
    0,                                  /* tp_as_mapping */
    0,                                  /* tp_hash  */
    0,                                  /* tp_call */
    0,                                  /* tp_str */
    0,                                  /* tp_getattro */
    0,                                  /* tp_setattro */
    0,                                  /* tp_as_buffer */
    Py_TPFLAGS_DEFAULT,                 /* tp_flags */
    "HeaderProtection objects",         /* tp_doc */
    0,                                  /* tp_traverse */
    0,                                  /* tp_clear */
    0,                                  /* tp_richcompare */
    0,                                  /* tp_weaklistoffset */
    0,                                  /* tp_iter */
    0,                                  /* tp_iternext */
    HeaderProtection_methods,           /* tp_methods */
    0,                                  /* tp_members */
    0,                                  /* tp_getset */
    0,                                  /* tp_base */
    0,                                  /* tp_dict */
    0,                                  /* tp_descr_get */
    0,                                  /* tp_descr_set */
    0,                                  /* tp_dictoffset */
    (initproc)HeaderProtection_init,    /* tp_init */
    0,                                  /* tp_alloc */
};


static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    MODULE_NAME,                        /* m_name */
    "A faster buffer.",                 /* m_doc */
    -1,                                 /* m_size */
    NULL,                               /* m_methods */
    NULL,                               /* m_reload */
    NULL,                               /* m_traverse */
    NULL,                               /* m_clear */
    NULL,                               /* m_free */
};

PyMODINIT_FUNC
PyInit__crypto(void)
{
    PyObject* m;

    m = PyModule_Create(&moduledef);
    if (m == NULL)
        return NULL;

    CryptoError = PyErr_NewException(MODULE_NAME ".CryptoError", PyExc_ValueError, NULL);
    Py_INCREF(CryptoError);
    PyModule_AddObject(m, "CryptoError", CryptoError);

    AEADType.tp_new = PyType_GenericNew;
    if (PyType_Ready(&AEADType) < 0)
        return NULL;
    Py_INCREF(&AEADType);
    PyModule_AddObject(m, "AEAD", (PyObject *)&AEADType);

    HeaderProtectionType.tp_new = PyType_GenericNew;
    if (PyType_Ready(&HeaderProtectionType) < 0)
        return NULL;
    Py_INCREF(&HeaderProtectionType);
    PyModule_AddObject(m, "HeaderProtection", (PyObject *)&HeaderProtectionType);

    return m;
}
