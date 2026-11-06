/*
 * pool-files.js
 * Finds a book's files on a subject site that keeps every file under an
 * opaque name (see tools/site/opaque.py). Load it after js/unlock.js, in
 * <head>, on a page that has
 *
 *   <meta name="pool-index" content="index.enc">
 *
 * and open the page as r.html#b=<book id>. The id sits after the "#", which
 * the browser never sends to the server, so no book id or name appears in
 * a server log. The page remembers it for this tab and removes it from the
 * address. (Links of the older form ?b=<id> still work.) Without that tag
 * the page is an
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

  const STORAGE_NAME = "rq-pool-book";

  /**
   * Take the book id from the page address, keep it for this tab and take
   * it out of the address. This runs when the script loads, before the
   * key handling rewrites the "#" part.
   * @returns {string} the book id, or ""
   */
  function bookIdAtLoad() {
    const hash = String(window.location.hash || "");
    const found = hash.match(/(?:^#|&)b=([^&]+)/);
    let id = found ? decodeURIComponent(found[1]) : "";
    if (id) {
      try {
        window.sessionStorage.setItem(STORAGE_NAME, id);
      } catch (problem) {
        // private mode: the id is kept in memory only
      }
      if (window.history && window.history.replaceState) {
        const rest = hash.slice(1).split("&").filter(function other(part) {
          return part && part.indexOf("b=") !== 0;
        }).join("&");
        window.history.replaceState(null, "", window.location.pathname +
          window.location.search + (rest ? "#" + rest : ""));
      }
      return id;
    }
    try {
      id = window.sessionStorage.getItem(STORAGE_NAME) || "";
    } catch (problem) {
      id = "";
    }
    return id || new URLSearchParams(window.location.search).get("b") || "";
  }

  const bookIdFromAddress = enabled ? bookIdAtLoad() : "";

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
          bookIdFromAddress, folderOf(meta.content));
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
