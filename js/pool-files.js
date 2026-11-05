/*
 * pool-files.js
 * Finds a book's files on a subject site that keeps every file under an
 * opaque name (see tools/site/opaque.py). Load it after js/unlock.js, in
 * <head>, on a page that has
 *
 *   <meta name="pool-index" content="index.enc">
 *
 * and open the page as ?b=<book id>. Without that tag the page is an
 * older book site and this file does nothing.
 *
 * index.enc is encrypted with the subject key. Once the reader has the key
 * (SubjectUnlock.ready) the file is read and decrypted, and
 *
 *   RecallQuizPool.ready()      resolves with the book's entry
 *   RecallQuizPool.addressOf(p) the address of the book's own file p, for
 *                               example "data/manifest.enc"
 *   RecallQuizPool.entry()      { id, title, summary, cover, views, files }
 *
 * work. A file the index does not list is an error, never a guess.
 *
 * A classic script with no dependencies.
 */
(function setUpPoolFiles() {
  "use strict";

  const meta = document.querySelector('meta[name="pool-index"]');
  const enabled = Boolean(meta);
  const SUPPORTED_FORMAT = 2;
  let current = null;
  let pending = null;

  /**
   * @param {string} indexAddress  where index.enc is, as the page sees it
   * @returns {string} the folder that holds the pool, ending in "/"
   */
  function folderOf(indexAddress) {
    return indexAddress.replace(/[^/]*$/, "");
  }

  /**
   * @returns {string} the book id in the page address (?b=...), or ""
   */
  function bookIdInAddress() {
    return new URLSearchParams(window.location.search).get("b") || "";
  }

  /**
   * Check the index and pick the book.
   * @param {object} index  the decrypted index.enc
   * @param {string} bookId  which book this page shows
   * @param {string} folder  the pool folder, ending in "/"
   * @returns {object} { entry, subject, addressOf }
   */
  function build(index, bookId, folder) {
    if (!index || index.format !== SUPPORTED_FORMAT) {
      throw new Error("This site was built with a newer version than " +
        "this page understands.");
    }
    const entry = (index.books || []).find(function same(book) {
      return book.id === bookId;
    });
    if (!entry) {
      throw new Error(bookId ? "This site has no such book." :
        "No book was named in the address.");
    }
    return {
      entry: entry,
      subject: index.subject || {},
      addressOf: function addressOf(path) {
        const found = entry.files && entry.files[path];
        if (!found) {
          throw new Error("Not in the book's file list: " + path);
        }
        return folder + found;
      },
    };
  }

  /**
   * @returns {Promise<object|null>} the book's entry; null when this page
   *   does not use the pool
   */
  function ready() {
    if (!enabled) {
      return Promise.resolve(null);
    }
    if (!pending) {
      const unlock = window.SubjectUnlock;
      pending = unlock.ready().then(function load(unlocked) {
        return fetch(meta.content, { cache: "no-cache" }).then(
          function read(reply) {
            if (!reply.ok) {
              throw new Error("index " + reply.status);
            }
            return reply.arrayBuffer();
          }).then(function open(buffer) {
          return unlock.decrypt(buffer, unlocked.key);
        });
      }).then(function parse(bytes) {
        current = build(JSON.parse(new TextDecoder().decode(bytes)),
          bookIdInAddress(), folderOf(meta.content));
        return current.entry;
      });
    }
    return pending;
  }

  /**
   * @param {string} path  a file name inside the book
   * @returns {string} its address (after ready() has resolved)
   */
  function addressOf(path) {
    if (!current) {
      throw new Error("The file list is not loaded yet.");
    }
    return current.addressOf(path);
  }

  window.RecallQuizPool = {
    enabled: enabled,
    ready: ready,
    addressOf: addressOf,
    entry: function entry() {
      return current && current.entry;
    },
    subject: function subject() {
      return current ? current.subject : null;
    },
    build: build,
  };
})();
