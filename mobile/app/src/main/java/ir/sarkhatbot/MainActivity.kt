package ir.sarkhatbot

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.SharedPreferences
import android.graphics.Color
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.view.KeyEvent
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Toast

/**
 * اپلیکیشن اندروید سرخط‌زن
 * =========================
 * یک WebView خیلی سبک که اپلیکیشن ربات را نشان می‌دهد.
 * آدرس پیش‌فرض: حالت ابری روی GitHub Pages — بدون نیاز به کامپیوتر یا Termux:
 *   https://mayanamin1813-cloud.github.io/sarkhat/
 * از منوی سه‌نقطه بالای صفحه می‌توانی آدرس را عوض کنی (مثلاً Termux محلی).
 */
class MainActivity : Activity() {

    companion object {
        const val DEFAULT_URL = "https://mayanamin1813-cloud.github.io/sarkhat/"
    }

    private lateinit var web: WebView
    private lateinit var prefs: SharedPreferences
    private var hadError = false          // آیا آخرین بار بارگذاری صفحه خطا داشت؟

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("sarkhat", MODE_PRIVATE)

        web = WebView(this)
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            cacheMode = WebSettings.LOAD_DEFAULT
            userAgentString = "$userAgentString SarkhatBotApp/2.0"
            textZoom = 100
        }
        web.setBackgroundColor(Color.parseColor("#0b1220"))
        web.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                hadError = false
            }
            override fun onReceivedError(view: WebView?, req: WebResourceRequest?, err: WebResourceError?) {
                if (req?.isForMainFrame == true && !hadError) {
                    hadError = true
                    runOnUiThread { showConnectionError() }
                }
            }
        }
        setContentView(web)

        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState)
        } else {
            val firstRun = prefs.getBoolean("first_run", true)
            web.loadUrl(prefs.getString("server_url", DEFAULT_URL)!!)
            if (firstRun) {
                askServerUrl(firstRun = true)
            }
        }
    }

    // ---------- منوی بالا ----------
    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, getString(R.string.menu_change_server))
        menu.add(0, 2, 0, getString(R.string.menu_reload))
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            1 -> { askServerUrl(false); true }
            2 -> { web.reload(); true }
            else -> super.onOptionsItemSelected(item)
        }
    }

    // ---------- دکمه برگشت ----------
    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK && web.canGoBack()) {
            web.goBack()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    // ---------- ذخیره وضعیت ----------
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    override fun onPause() {
        super.onPause()
        web.onPause()
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
    }

    // ---------- گفت‌وگوها ----------
    private fun askServerUrl(firstRun: Boolean) {
        val box = LinearLayout(this)
        box.setPadding(48, 24, 48, 0)
        val input = EditText(this)
        input.setText(prefs.getString("server_url", DEFAULT_URL))
        input.hint = DEFAULT_URL
        box.addView(input, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))

        AlertDialog.Builder(this)
            .setTitle(R.string.server_dialog_title)
            .setMessage(R.string.server_dialog_hint)
            .setView(box)
            .setPositiveButton(R.string.server_dialog_ok) { _, _ ->
                var url = input.text.toString().trim().trimEnd('/')
                if (url.isNotEmpty() && !url.startsWith("http")) url = "http://$url"
                prefs.edit().putString("server_url", url)
                    .putBoolean("first_run", false).apply()
                web.loadUrl(url)
                Toast.makeText(this, url, Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton(R.string.server_dialog_cancel) { d, _ ->
                d.dismiss()
                prefs.edit().putBoolean("first_run", false).apply()
            }
            .setCancelable(!firstRun)
            .show()
    }

    private fun showConnectionError() {
        AlertDialog.Builder(this)
            .setTitle("اتصال برقرار نشد")
            .setMessage(
                "ربات سرخط‌زن روی این آدرس در دسترس نیست.\n\n" +
                "۱) اینترنت گوشی را چک کن و «تلاش دوباره» بزن\n\n" +
                "۲) اگر ربات را با Termux روی همین گوشی اجرا کرده‌ای، در Termux بزن:\n" +
                "    python main.py serve\n" +
                "    و بعد از منوی سه‌نقطه آدرس را بگذار: http://localhost:8000\n\n" +
                "۳) حالت ابری (پیش‌فرض، بدون کامپیوتر):\n" +
                "    " + DEFAULT_URL
            )
            .setPositiveButton("تلاش دوباره") { _, _ -> web.reload() }
            .setNegativeButton("تغییر آدرس") { _, _ -> askServerUrl(false) }
            .setNeutralButton("حالت ابری") { _, _ ->
                prefs.edit().putString("server_url", DEFAULT_URL).apply()
                web.loadUrl(DEFAULT_URL)
            }
            .setCancelable(false)
            .show()
    }
}
