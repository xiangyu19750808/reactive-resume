// /srv/wxresume/render/index.js
const express = require("express");
const { execFile } = require("child_process");
const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer");

const app = express();
app.use(express.json({ limit: "2mb" }));

app.post("/render", async (req, res) => {
  try {
    const resume = req.body.resume || {};
    const theme = req.body.theme || "jsonresume-theme-even";
    const id = Date.now().toString();
    const jsonPath = `/tmp/${id}.json`;
    const htmlPath = `/tmp/${id}.html`;
    const pdfPath = `/tmp/${id}.pdf`;

    fs.writeFileSync(jsonPath, JSON.stringify(resume, null, 2));

    const resumeCli = path.join(__dirname, "node_modules", ".bin", "resume");
    await new Promise((resolve, reject) => {
      execFile(
        resumeCli,
        ["export", htmlPath, "--resume", jsonPath, "--theme", theme],
        { env: process.env },
        (err) => (err ? reject(err) : resolve())
      );
    });

    const browser = await puppeteer.launch({
      executablePath: process.env.PUPPETEER_EXECUTABLE_PATH,
      args: ["--no-sandbox", "--disable-setuid-sandbox"],
    });
    const page = await browser.newPage();
    await page.goto(`file://${htmlPath}`, { waitUntil: "networkidle0" });
    await page.pdf({ path: pdfPath, format: "A4", printBackground: true });
    await browser.close();

    res.json({ ok: true, html_path: htmlPath, pdf_path: pdfPath });
  } catch (e) {
    res.status(500).json({ ok: false, msg: String(e) });
  }
});

app.listen(3000, () => console.log("render up @3000"));
