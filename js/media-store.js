/*
 * media-store.js
 * Finds and opens a book's pictures and clips.
 *
 * A media entry in questions.json looks like
 *   { "id": "u03/i07/f02", "alt": "text for the picture",
 *     "role": "figure", "type": "image/webp" }
 *
 * In the book's own repo the file is plain: media/<id>.<ext>, where the
 * extension follows "type" (image/webp gives media/u03/i07/f02.webp; a
 * missing type means image/webp). It is shown as it is.
 *
 * On an encrypted book site the same entry is stored encrypted under the
 * subject key, and the page that knows the key (js/encrypted-book.js) sets a
 * key provider here; then the picture is fetched from <mediaBase><id>.enc
 * and opened with that key (on a pool site through the index, see
 * js/pool-files.js). An entry with a "src" instead of an "id" is a plain
 * file that is shown as it is (for the author's own diagrams).
 *
 * The subject key is only in memory. Without it on an encrypted site, or
 * without Web Crypto, load() gives null and the page shows the text instead
 * of the picture. questions.json "mediaBase" changes the folder. Files are
 * fetched when a card or question asks for them, never all at the start.
 */
(function setUpMediaStore(quiz) {
  "use strict";

  const NONCE_BYTES = 12;
  const media = { keyProvider: null, keyLocation: "" };
  /** id -> why a file could not be opened (words for the reader) */
  const reasons = new Map();
  let keyReason = "";
  /** id -> promise of an object URL (or null) */
  const loaded = new Map();
  /** The subject key once it has been imported, or null */
  let keyPromise = null;

  /**
   * Set the function that supplies the subject key (base64) or null.
   * @param {function(): Promise<string|null>} provider
   */
  function setKeyProvider(provider) {
    media.keyProvider = provider;
    keyPromise = null;
    keyReason = "";
    loaded.clear();
    reasons.clear();
    // Pictures that showed their words because there was no key try again.
    document.dispatchEvent(new CustomEvent("recallquiz:media-key-changed"));
  }

  /**
   * The text of base64 as bytes.
   * @param {string} text
   * @returns {Uint8Array}
   */
  function fromBase64(text) {
    const raw = atob(text.trim());
    return Uint8Array.from(raw, function toByte(char) {
      return char.charCodeAt(0);
    });
  }

  /** Extension of a plain file for each media type. */
  const EXTENSIONS = {
    "image/webp": "webp", "image/png": "png", "image/jpeg": "jpg",
    "image/gif": "gif", "image/svg+xml": "svg", "image/avif": "avif",
    "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/ogg": "ogg",
    "audio/wav": "wav", "video/mp4": "mp4", "video/webm": "webm",
  };

  /**
   * The address of a plain file of the book: <base><id>.<ext>.
   * @param {object} entry  { id, type }
   * @returns {string}
   */
  function plainAddress(entry) {
    const base = (quiz.book.data && quiz.book.data.mediaBase) || "media/";
    const extension = EXTENSIONS[entry.type || "image/webp"] || "webp";
    return base + entry.id + "." + extension;
  }

  /**
   * A plain file: checked once so a missing file gives a reason, then shown
   * as it is.
   * @param {object} entry  { id, type }
   * @returns {Promise<string|null>}
   */
  async function openPlain(entry) {
    const address = plainAddress(entry);
    const response = await fetch(address, { method: "HEAD" });
    if (response.status === 405 || response.status === 501) {
      return address;   // a server that does not answer HEAD: just try it
    }
    if (!response.ok) {
      reasons.set(entry.id, "file not found (HTTP " + response.status +
        ") at " + address);
      return null;
    }
    return address;
  }

  /**
   * The imported subject key, or null when there is none.
   * @returns {Promise<CryptoKey|null>}
   */
  function getKey() {
    if (!keyPromise) {
      keyPromise = importKey();
    }
    return keyPromise;
  }

  /** @returns {Promise<CryptoKey|null>} */
  async function importKey() {
    if (!window.crypto || !crypto.subtle) {
      keyReason = "this browser cannot decrypt (needs https)";
      return null;
    }
    if (!media.keyProvider) {
      keyReason = "no key";
      return null;
    }
    try {
      const encoded = await media.keyProvider();
      if (!encoded) {
        keyReason = "no key found" + (media.keyLocation ?
          " at " + media.keyLocation : "");
        return null;
      }
      return await crypto.subtle.importKey(
        "raw", fromBase64(encoded), "HKDF", false, ["deriveKey"]);
    } catch (error) {
      keyReason = error && error.code === "read" ?
        "could not read the key at " + (media.keyLocation || "the key store") +
          ": " + error.message :
        "the key is not valid base64";
      return null;
    }
  }

  /**
   * The AES key for one file.
   * @param {CryptoKey} key
   * @param {string} id
   * @returns {Promise<CryptoKey>}
   */
  function deriveFileKey(key, id) {
    const info = new TextEncoder().encode("file:" + id);
    return crypto.subtle.deriveKey(
      { name: "HKDF", hash: "SHA-256", salt: new Uint8Array(0), info },
      key, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  }

  /**
   * A picture on a pool site: found through the index, stored as an RQE1
   * file under the subject key (see tools/site/opaque.py).
   * @param {object} pool  window.RecallQuizPool
   * @param {string} path  the picture's name inside the book
   * @param {object} entry  { id, type }
   * @returns {Promise<string|null>}
   */
  async function openFromPool(pool, path, entry) {
    try {
      await pool.ready();
      const response = await fetch(pool.addressOf(path));
      if (!response.ok) {
        reasons.set(entry.id, "file not found (HTTP " + response.status + ")");
        return null;
      }
      const plain = await window.SubjectUnlock.decrypt(
        await response.arrayBuffer(), window.RecallQuizSubjectKey);
      return URL.createObjectURL(
        new Blob([plain], { type: entry.type || "image/webp" }));
    } catch (error) {
      reasons.set(entry.id, error.message);
      return null;
    }
  }

  /**
   * Open one file: plain in the book's repo, decrypted on an encrypted site.
   * @param {object} entry  { id, type }
   * @returns {Promise<string|null>}
   */
  async function openEncrypted(entry) {
    if (!media.keyProvider) {
      return openPlain(entry);   // the book's own repo: plain files
    }
    const key = await getKey();
    if (!key) {
      reasons.set(entry.id, keyReason);
      return null;
    }
    const base = (quiz.book.data && quiz.book.data.mediaBase) || "media/";
    const pool = window.RecallQuizPool;
    if (pool && pool.enabled) {
      return openFromPool(pool, base + entry.id + ".enc", entry);
    }
    const response = await fetch(base + entry.id + ".enc");
    if (!response.ok) {
      reasons.set(entry.id, "file not found (HTTP " + response.status +
        "): is this branch published?");
      return null;
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    const fileKey = await deriveFileKey(key, entry.id);
    let plain;
    try {
      plain = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: bytes.slice(0, NONCE_BYTES),
          additionalData: new TextEncoder().encode(entry.id) },
        fileKey, bytes.slice(NONCE_BYTES));
    } catch (error) {
      reasons.set(entry.id, "the key does not match these files " +
        "(publish the book again with the subject key)");
      return null;
    }
    const blob = new Blob([plain], { type: entry.type || "image/webp" });
    return URL.createObjectURL(blob);
  }

  /**
   * A web address for one media entry, or null when it cannot be shown.
   * Never throws.
   * @param {object} entry
   * @returns {Promise<string|null>}
   */
  function load(entry) {
    if (entry.src) {
      return Promise.resolve(entry.src);
    }
    if (!entry.id) {
      return Promise.resolve(null);
    }
    if (!loaded.has(entry.id)) {
      loaded.set(entry.id, openEncrypted(entry).catch(function fail(error) {
        reasons.set(entry.id, "could not be opened: " + error.message);
        return null;
      }));
    }
    return loaded.get(entry.id);
  }

  /**
   * Start fetching entries so they are ready when the reader gets there.
   * @param {object[]} entries
   */
  function prefetch(entries) {
    (entries || []).forEach(load);
  }

  /** @returns {string} why a file did not open, or "" */
  media.reasonFor = function reasonFor(id) {
    return reasons.get(id) || "";
  };
  media.setKeyProvider = setKeyProvider;
  media.load = load;
  media.prefetch = prefetch;
  quiz.media = media;
})(window.RecallQuiz = window.RecallQuiz || {});
