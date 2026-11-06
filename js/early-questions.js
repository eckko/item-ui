/*
 * early-questions.js
 * Starts downloading the question bank as soon as the page begins to
 * load, so the download runs while the other scripts load instead of
 * after them. A big book (several MB) is the slowest thing to arrive.
 *
 * This file is loaded in <head>. It only starts the request and keeps the
 * promise on window.RecallQuizQuestions.promise; js/main.js uses it. The
 * file is still checked with the server every time (cache "no-cache"),
 * so an updated book always shows up: the browser sends the saved copy's
 * tag and gets back a tiny "not modified" answer when nothing changed.
 *
 * It uses no other file and has no dependencies.
 */
(function startQuestionsDownload() {
  "use strict";

  const QUESTIONS_FILE = "questions.json";

  /**
   * The bank to load: questions.json, or a file in banks/ named in the
   * address (index.html?bank=banks/reading-method.json), which lets
   * one site hold several sample banks.
   * @returns {string}
   */
  function questionsFileName() {
    const wanted = new URLSearchParams(window.location.search).get("bank");
    return /^banks\/[\w-]+\.json$/.test(wanted || "") ?
      wanted : QUESTIONS_FILE;
  }

  /**
   * Join the chunks of a split bank (see tools/site/split_questions.py).
   * The first chunk carries id, book, labels and so on; the questions of
   * all chunks follow in the order given.
   * @param {object[]} parts  the parsed chunks, in book order
   * @returns {object} one bank, as questions.json would be
   */
  function mergeParts(parts) {
    const bank = Object.assign({}, parts[0]);
    bank.questions = [].concat.apply([], parts.map(function list(part) {
      return part.questions || [];
    }));
    return bank;
  }

  /**
   * Encrypted book site (template v3): the page set
   * window.RecallQuizEncrypted = { questions: "quiz/questions.enc" } and
   * unlocked the subject key (js/unlock.js) before starting this file.
   * On a pool site a big bank is stored in chunks, one per unit; the
   * book's index entry then lists them in "questionParts" and they are
   * fetched together. Without that list the single file is read.
   * @returns {Promise<object>} the decrypted, parsed question bank
   */
  function fetchEncryptedQuestions() {
    const unlock = window.SubjectUnlock;
    const pool = window.RecallQuizPool;
    const usePool = Boolean(pool && pool.enabled);

    function fetchOne(address, key) {
      return fetch(address, { cache: "no-cache" })
        .then(function read(response) {
          if (!response.ok) {
            throw new Error("HTTP " + response.status);
          }
          return response.arrayBuffer();
        }).then(function open(buffer) {
          return unlock.decrypt(buffer, key);
        }).then(function parse(bytes) {
          return JSON.parse(new TextDecoder().decode(bytes));
        });
    }

    return unlock.ready().then(function find(unlocked) {
      // a pool site names the file through its encrypted index
      return (usePool ? pool.ready() : Promise.resolve(null))
        .then(function fetchAll(entry) {
          const key = unlocked.key;
          if (usePool && entry && entry.questionParts &&
              entry.questionParts.length) {
            return Promise.all(entry.questionParts.map(
              function one(path) {
                return fetchOne(pool.addressOf(path), key);
              })).then(mergeParts);
          }
          return fetchOne(usePool ?
            pool.addressOf(window.RecallQuizEncrypted.questions) :
            window.RecallQuizEncrypted.questions, key);
        });
    });
  }

  /**
   * Ask for the question bank.
   * @returns {Promise<object>} the parsed JSON; it rejects when the file
   *   cannot be read (for example when the page is opened from a folder)
   */
  function fetchQuestions() {
    if (window.RecallQuizEncrypted) {
      return fetchEncryptedQuestions();
    }
    return fetch(questionsFileName(), { cache: "no-cache" })
      .then(function readJson(response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      });
  }

  const promise = fetchQuestions();
  // main.js handles a failure; this stops a "not handled" warning in the
  // short time before it has attached its own handler.
  promise.catch(function ignoreHere() {});
  window.RecallQuizQuestions = { promise, mergeParts };
})();
