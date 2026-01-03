#!/usr/bin/env python3
# 文件名: login_script.py
# 作用: 自动登录 ClawCloud Run，支持 GitHub 账号密码 + 2FA 自动验证
# 优化版本: 增强的选择器、重试机制、详细日志

import os
import sys
import time
import pyotp  # 用于生成 2FA 验证码
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ==================== 配置项 ====================
CLAW_CLOUD_URL = "https://ap-northeast-1.run.claw.cloud/"
MAX_2FA_RETRIES = 3  # 2FA 验证码重试次数
WAIT_AFTER_2FA = 5   # 2FA 提交后等待秒数
FINAL_WAIT = 25      # 最终跳转等待秒数


def log_step(msg, level="INFO"):
    """统一日志输出"""
    icons = {
        "INFO": "ℹ️",
        "SUCCESS": "✅",
        "ERROR": "❌",
        "WARN": "⚠️",
        "STEP": "🔹"
    }
    icon = icons.get(level, "•")
    print(f"{icon} {msg}")


def safe_screenshot(page, filename, description=""):
    """安全截图（即使失败也不中断）"""
    try:
        page.screenshot(path=filename, full_page=True)
        log_step(f"已保存截图: {filename}", "SUCCESS")
        if description:
            log_step(f"  说明: {description}")
        return True
    except Exception as e:
        log_step(f"截图失败: {e}", "WARN")
        return False


def try_click(page, selectors, description="按钮", timeout=5000):
    """尝试多个选择器点击（智能查找）"""
    for selector in selectors:
        try:
            element = page.locator(selector).first
            if element.is_visible(timeout=timeout):
                element.click()
                log_step(f"已点击: {description} ({selector})", "SUCCESS")
                return True
        except:
            continue
    log_step(f"未找到: {description}", "WARN")
    return False


def fill_github_credentials(page, username, password):
    """填写 GitHub 登录凭据"""
    log_step("🔒 检测到 GitHub 登录页面", "STEP")
    
    # 等待登录表单加载
    try:
        page.wait_for_selector("#login_field", state="visible", timeout=10000)
        
        # 清空并填写用户名
        page.fill("#login_field", "")
        page.fill("#login_field", username)
        log_step(f"已填写用户名: {username[:3]}***")
        
        # 清空并填写密码
        page.fill("#password", "")
        page.fill("#password", password)
        log_step("已填写密码: ********")
        
        # 截图
        safe_screenshot(page, "01_credentials_filled.png", "凭据已填写")
        
        # 提交表单
        submit_selectors = [
            "input[name='commit']",
            "input[type='submit']",
            "button[type='submit']",
            "button:has-text('Sign in')"
        ]
        
        if try_click(page, submit_selectors, "登录按钮"):
            log_step("登录表单已提交", "SUCCESS")
            return True
        else:
            log_step("找不到提交按钮", "ERROR")
            safe_screenshot(page, "error_no_submit_button.png")
            return False
            
    except Exception as e:
        log_step(f"填写凭据失败: {e}", "ERROR")
        safe_screenshot(page, "error_fill_credentials.png")
        return False


def handle_2fa_verification(page, totp_secret):
    """处理 2FA 双重验证（支持多种页面格式）"""
    log_step("🔐 检测到 2FA 双重验证", "STEP")
    safe_screenshot(page, "02_2fa_page.png", "2FA 验证页面")
    
    if not totp_secret:
        log_step("未配置 GH_2FA_SECRET，无法自动填写验证码", "ERROR")
        log_step("请在 GitHub Secrets 中添加 GH_2FA_SECRET", "ERROR")
        return False
    
    # 所有可能的 2FA 输入框选择器
    input_selectors = [
        "#app_totp",              # App 验证（最常见）
        "#otp",                   # 标准 OTP
        "#sms_otp",               # 短信验证
        "input[name='otp']",
        "input[name='app_otp']",
        "input[autocomplete='one-time-code']",
        "input[type='text'][inputmode='numeric']",
        "input[aria-label*='code' i]",
        "input[placeholder*='code' i]",
        "input.form-control[type='text']"
    ]
    
    # 所有可能的提交按钮选择器
    submit_selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Verify')",
        "button:has-text('验证')",
        "button.btn-primary"
    ]
    
    for attempt in range(MAX_2FA_RETRIES):
        log_step(f"🔢 尝试 {attempt + 1}/{MAX_2FA_RETRIES}...", "STEP")
        
        # 生成新的验证码
        try:
            totp = pyotp.TOTP(totp_secret)
            code = totp.now()
            log_step(f"生成验证码: {code}", "SUCCESS")
        except Exception as e:
            log_step(f"生成验证码失败: {e}", "ERROR")
            return False
        
        # 查找输入框
        input_element = None
        used_selector = None
        
        for selector in input_selectors:
            try:
                element = page.locator(selector).first
                if element.is_visible(timeout=2000):
                    input_element = element
                    used_selector = selector
                    log_step(f"找到输入框: {selector}", "SUCCESS")
                    break
            except:
                continue
        
        if not input_element:
            log_step("未找到任何 2FA 输入框", "ERROR")
            safe_screenshot(page, "error_no_2fa_input.png")
            
            # 尝试等待页面加载
            log_step("等待页面完全加载...", "WARN")
            time.sleep(3)
            
            # 最后一次尝试
            if attempt == MAX_2FA_RETRIES - 1:
                return False
            continue
        
        # 填写验证码
        try:
            # 清空输入框
            input_element.clear()
            time.sleep(0.5)
            
            # 填入验证码
            input_element.fill(code)
            log_step(f"已填入验证码: {code}", "SUCCESS")
            time.sleep(0.5)
            
            # 截图
            safe_screenshot(page, f"03_2fa_code_entered_{attempt+1}.png", f"验证码已输入 (尝试{attempt+1})")
            
            # 查找并点击提交按钮
            submit_clicked = False
            for selector in submit_selectors:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        log_step(f"已点击提交按钮: {selector}", "SUCCESS")
                        submit_clicked = True
                        break
                except:
                    continue
            
            # 如果没有找到提交按钮，尝试按回车键
            if not submit_clicked:
                log_step("未找到提交按钮，尝试按回车键", "WARN")
                input_element.press("Enter")
                log_step("已按回车键提交", "SUCCESS")
            
            # 等待页面响应
            log_step(f"等待 {WAIT_AFTER_2FA} 秒，检查验证结果...", "INFO")
            time.sleep(WAIT_AFTER_2FA)
            
            # 等待网络空闲
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeout:
                log_step("页面加载超时，继续检查...", "WARN")
            
            # 检查是否验证成功
            current_url = page.url
            log_step(f"当前 URL: {current_url}")
            
            # 成功的标志：已离开 2FA 页面
            if 'two-factor' not in current_url and 'sessions/two-factor' not in current_url:
                log_step("2FA 验证成功！", "SUCCESS")
                safe_screenshot(page, "04_2fa_success.png", "2FA 验证成功")
                return True
            
            # 检查是否有错误提示
            try:
                error_selectors = [
                    ".flash-error",
                    ".js-flash-alert",
                    "[role='alert']"
                ]
                for err_sel in error_selectors:
                    error_elem = page.locator(err_sel).first
                    if error_elem.is_visible(timeout=1000):
                        error_text = error_elem.inner_text()
                        log_step(f"错误提示: {error_text}", "ERROR")
                        break
            except:
                pass
            
            # 验证码可能已过期，等待下一个周期
            log_step(f"验证码 {code} 验证失败，可能已过期", "WARN")
            
            if attempt < MAX_2FA_RETRIES - 1:
                log_step("等待新验证码生成（30秒周期）...", "INFO")
                time.sleep(32 - (time.time() % 30))  # 等到下一个30秒周期
            
        except Exception as e:
            log_step(f"填写验证码异常: {e}", "ERROR")
            safe_screenshot(page, f"error_2fa_fill_{attempt+1}.png")
            
            if attempt < MAX_2FA_RETRIES - 1:
                time.sleep(3)
                continue
    
    # 所有尝试失败
    log_step(f"2FA 验证失败（已尝试 {MAX_2FA_RETRIES} 次）", "ERROR")
    return False


def handle_device_verification(page):
    """处理设备验证（邮件/App 批准）"""
    log_step("📧 检测到设备验证请求", "WARN")
    safe_screenshot(page, "device_verification.png", "设备验证页面")
    
    log_step("请在 60 秒内完成以下操作之一:", "WARN")
    log_step("  1. 检查邮箱并点击验证链接", "INFO")
    log_step("  2. 在 GitHub App 中批准设备", "INFO")
    log_step("  3. 访问 https://github.com/settings/security", "INFO")
    
    # 等待 60 秒
    for i in range(60):
        time.sleep(1)
        
        if i % 10 == 0:
            log_step(f"  等待中... ({i}/60 秒)")
            try:
                current_url = page.url
                # 检查是否已离开验证页面
                if 'verified-device' not in current_url and 'device-verification' not in current_url:
                    log_step("设备验证完成！", "SUCCESS")
                    return True
                
                # 尝试刷新页面状态
                page.evaluate("() => {}")
            except:
                pass
    
    # 超时后最终检查
    try:
        final_url = page.url
        if 'verified-device' not in final_url and 'device-verification' not in final_url:
            log_step("设备验证完成！", "SUCCESS")
            return True
    except:
        pass
    
    log_step("设备验证超时", "ERROR")
    return False


def handle_oauth_authorization(page):
    """处理 OAuth 授权页面"""
    log_step("🔓 检测到授权请求页面", "STEP")
    safe_screenshot(page, "05_oauth_authorize.png", "OAuth 授权")
    
    authorize_selectors = [
        "button[name='authorize']",
        "button:has-text('Authorize')",
        "input[name='authorize']",
        "button.btn-primary:has-text('Authorize')"
    ]
    
    if try_click(page, authorize_selectors, "Authorize 按钮", timeout=3000):
        log_step("已点击授权", "SUCCESS")
        time.sleep(3)
        return True
    else:
        log_step("未找到授权按钮，可能自动跳过", "WARN")
        return False


def verify_login_success(page):
    """验证是否登录成功"""
    log_step("🔍 验证登录状态...", "STEP")
    
    final_url = page.url
    log_step(f"最终 URL: {final_url}")
    
    # 多重检查机制
    success_indicators = []
    
    # 检查 1: URL 特征
    if 'claw.cloud' in final_url and 'signin' not in final_url:
        success_indicators.append("URL 正确")
    
    # 检查 2: 不在 GitHub 验证页
    if 'github.com' not in final_url:
        success_indicators.append("已离开 GitHub")
    
    # 检查 3: 页面特征文字
    page_text_checks = [
        ("App Launchpad", "应用启动台"),
        ("Devbox", "开发环境"),
        ("Dashboard", "控制台"),
        ("Create", "创建按钮"),
        ("Workspace", "工作空间")
    ]
    
    for text, description in page_text_checks:
        try:
            if page.get_by_text(text).count() > 0:
                success_indicators.append(f"找到'{description}'")
                break
        except:
            continue
    
    # 检查 4: 特定元素
    try:
        # 检查是否有用户菜单等登录后才有的元素
        user_menu_selectors = [
            "[data-testid='user-menu']",
            "button[aria-label*='user' i]",
            ".user-avatar",
            "[class*='avatar']"
        ]
        for selector in user_menu_selectors:
            if page.locator(selector).count() > 0:
                success_indicators.append("找到用户菜单")
                break
    except:
        pass
    
    log_step(f"成功指标: {', '.join(success_indicators) if success_indicators else '无'}")
    
    # 至少需要 2 个成功指标
    is_success = len(success_indicators) >= 2
    
    return is_success


def run_login():
    """主登录流程"""
    print("\n" + "="*60)
    print("🚀 ClawCloud 自动登录脚本 (优化版 v2.0)")
    print("="*60 + "\n")
    
    # 1. 获取环境变量
    username = os.environ.get("GH_USERNAME")
    password = os.environ.get("GH_PASSWORD")
    totp_secret = os.environ.get("GH_2FA_SECRET")
    
    log_step("配置检查:", "STEP")
    log_step(f"  用户名: {username[:3]}*** (已设置)" if username else "  用户名: 未设置 ❌", "INFO")
    log_step(f"  密码: ******** (已设置)" if password else "  密码: 未设置 ❌", "INFO")
    log_step(f"  2FA Secret: {'已设置 ✅' if totp_secret else '未设置 ⚠️'}", "INFO")
    
    if not username or not password:
        log_step("错误: 必须设置 GH_USERNAME 和 GH_PASSWORD 环境变量", "ERROR")
        log_step("请在 GitHub Secrets 中配置这些值", "ERROR")
        sys.exit(1)
    
    print()
    
    # 2. 启动浏览器
    log_step("启动浏览器...", "STEP")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage'
            ]
        )
        
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        page = context.new_page()
        
        try:
            # 3. 访问 ClawCloud
            log_step(f"访问 ClawCloud: {CLAW_CLOUD_URL}", "STEP")
            page.goto(CLAW_CLOUD_URL, timeout=60000)
            page.wait_for_load_state("networkidle", timeout=30000)
            time.sleep(2)
            safe_screenshot(page, "00_clawcloud_home.png", "ClawCloud 首页")
            
            # 4. 点击 GitHub 登录按钮
            log_step("查找 GitHub 登录按钮...", "STEP")
            github_button_selectors = [
                "button:has-text('GitHub')",
                "a:has-text('GitHub')",
                "[data-provider='github']",
                "button[data-test='github-login']",
                ".github-login-button"
            ]
            
            if not try_click(page, github_button_selectors, "GitHub 登录按钮", timeout=10000):
                # 可能已经登录
                if 'signin' not in page.url.lower():
                    log_step("可能已经登录，跳过 GitHub 按钮", "WARN")
                else:
                    log_step("找不到 GitHub 登录按钮", "ERROR")
                    safe_screenshot(page, "error_no_github_button.png")
                    sys.exit(1)
            
            # 5. 等待跳转到 GitHub
            log_step("等待跳转到 GitHub...", "STEP")
            time.sleep(3)
            page.wait_for_load_state("networkidle", timeout=30000)
            
            current_url = page.url
            log_step(f"当前 URL: {current_url}")
            
            # 6. 处理 GitHub 登录
            if "github.com/login" in current_url or "github.com/session" in current_url:
                if not fill_github_credentials(page, username, password):
                    log_step("GitHub 登录失败", "ERROR")
                    sys.exit(1)
                
                # 等待登录响应
                time.sleep(3)
                page.wait_for_load_state("networkidle", timeout=30000)
                current_url = page.url
                log_step(f"登录后 URL: {current_url}")
            
            # 7. 处理设备验证（如果需要）
            if 'verified-device' in current_url or 'device-verification' in current_url:
                if not handle_device_verification(page):
                    log_step("设备验证失败", "ERROR")
                    sys.exit(1)
                current_url = page.url
            
            # 8. 处理 2FA（如果需要）
            if 'two-factor' in current_url or 'sessions/two-factor' in current_url:
                if not handle_2fa_verification(page, totp_secret):
                    log_step("2FA 验证失败", "ERROR")
                    safe_screenshot(page, "final_error_2fa.png")
                    sys.exit(1)
                current_url = page.url
            
            # 9. 处理 OAuth 授权（如果需要）
            time.sleep(2)
            if 'github.com/login/oauth/authorize' in current_url:
                handle_oauth_authorization(page)
                time.sleep(3)
                page.wait_for_load_state("networkidle", timeout=30000)
            
            # 10. 等待最终跳转
            log_step(f"等待最终跳转 ({FINAL_WAIT} 秒)...", "STEP")
            time.sleep(FINAL_WAIT)
            
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeout:
                log_step("页面加载超时，继续验证...", "WARN")
            
            # 11. 验证登录成功
            safe_screenshot(page, "99_final_result.png", "最终登录结果")
            
            if verify_login_success(page):
                log_step("="*60, "SUCCESS")
                log_step("🎉 登录成功！", "SUCCESS")
                log_step("="*60, "SUCCESS")
                print()
            else:
                log_step("="*60, "ERROR")
                log_step("登录失败或无法确认", "ERROR")
                log_step("请下载截图查看详情", "ERROR")
                log_step("="*60, "ERROR")
                sys.exit(1)
            
        except KeyboardInterrupt:
            log_step("用户中断", "WARN")
            sys.exit(130)
            
        except Exception as e:
            log_step(f"发生异常: {e}", "ERROR")
            safe_screenshot(page, "exception_error.png")
            
            import traceback
            print("\n" + "="*60)
            print("详细错误信息:")
            print("="*60)
            traceback.print_exc()
            print("="*60 + "\n")
            
            sys.exit(1)
            
        finally:
            browser.close()


if __name__ == "__main__":
    run_login()
