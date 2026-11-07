/**
 * 川投顧 Google Apps Script Web App
 * 綁定既有 Google 試算表，客戶名單使用「工作表1」A 欄。
 * 公告防重複紀錄使用「公告紀錄」工作表。
 */
function jsonResponse(data) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    const action = String(body.action || "");
    const ss = SpreadsheetApp.getActiveSpreadsheet();

    if (action === "add") {
      const userId = String(body.userId || "").trim();
      if (!/^U[A-Za-z0-9]{9,}$/.test(userId)) {
        throw new Error("LINE UID 格式不正確");
      }
      const lock = LockService.getScriptLock();
      lock.waitLock(10000);
      try {
        const sheet = ss.getSheetByName("工作表1");
        if (!sheet) throw new Error("找不到工作表1");
        const lastRow = sheet.getLastRow();
        const values = lastRow > 0
          ? sheet.getRange(1, 1, lastRow, 1).getDisplayValues().flat()
          : [];
        const exists = values.some(v => String(v).trim() === userId);
        if (!exists) sheet.appendRow([userId]);
        SpreadsheetApp.flush();
        const verifyLast = sheet.getLastRow();
        const verifyValues = verifyLast > 0
          ? sheet.getRange(1, 1, verifyLast, 1).getDisplayValues().flat()
          : [];
        const verified = verifyValues.some(v => String(v).trim() === userId);
        if (!verified) throw new Error("UID 寫入後驗證失敗");
        return jsonResponse({
          status: "success", action: "add",
          added: !exists, verified: true
        });
      } finally {
        lock.releaseLock();
      }
    }

    if (action === "get") {
      const sheet = ss.getSheetByName("工作表1");
      if (!sheet) throw new Error("找不到工作表1");
      const lastRow = sheet.getLastRow();
      const values = lastRow > 0
        ? sheet.getRange(1, 1, lastRow, 1).getDisplayValues().flat()
        : [];
      const seen = {};
      const userIds = values.map(v => String(v).trim())
        .filter(v => /^U[A-Za-z0-9]{9,}$/.test(v))
        .filter(v => {
          if (seen[v]) return false;
          seen[v] = true;
          return true;
        });
      return jsonResponse({status: "success", userIds: userIds});
    }

    if (action === "claim_announcement") {
      const eventId = String(body.eventId || "").trim();
      const announcementKey = String(body.announcementKey || "").trim();
      const userId = String(body.userId || "").trim();
      if (!eventId || eventId.length > 200 ||
          !/^[a-f0-9]{64}$/i.test(announcementKey) ||
          !/^U[A-Za-z0-9]{9,}$/.test(userId)) {
        throw new Error("公告防重複資料格式不正確");
      }

      const lock = LockService.getScriptLock();
      lock.waitLock(10000);
      try {
        let sheet = ss.getSheetByName("公告紀錄");
        if (!sheet) {
          sheet = ss.insertSheet("公告紀錄");
          sheet.appendRow(["event_id", "announcement_key", "user_id", "created_at"]);
        }
        if (sheet.getLastRow() === 0) {
          sheet.appendRow(["event_id", "announcement_key", "user_id", "created_at"]);
        }
        const lastRow = sheet.getLastRow();
        const rows = lastRow > 1
          ? sheet.getRange(2, 1, lastRow - 1, 4).getValues()
          : [];
        const now = new Date();
        const cutoff = now.getTime() - 5 * 60 * 1000;
        const duplicate = rows.some(row => {
          const oldEvent = String(row[0] || "");
          const oldKey = String(row[1] || "");
          const oldTime = row[3] instanceof Date
            ? row[3].getTime() : new Date(row[3]).getTime();
          return oldEvent === eventId ||
            (oldKey === announcementKey && isFinite(oldTime) && oldTime >= cutoff);
        });
        if (duplicate) {
          return jsonResponse({status: "success", claimed: false, duplicate: true});
        }
        sheet.appendRow([eventId, announcementKey, userId, now]);
        SpreadsheetApp.flush();
        return jsonResponse({status: "success", claimed: true, duplicate: false});
      } finally {
        lock.releaseLock();
      }
    }

    throw new Error("不支援的 action");
  } catch (error) {
    return jsonResponse({status: "error", message: String(error && error.message || error)});
  }
}
