# Keep Chaquopy Python runtime, JNI bridges, and embedded Python package references
-keep class com.chaquo.python.** { *; }
-dontwarn com.chaquo.python.**

# Keep Beamer application, AccessibilityService, LinkService, and PermissionManager entry points
-keep class uk.co.kalkman.beamer.** { *; }

# Keep Kotlin Coroutines & Compose metadata safe under R8
-keepnames class kotlinx.coroutines.internal.MainDispatcherFactory {}
-keepnames class kotlinx.coroutines.CoroutineExceptionHandler {}
-dontwarn kotlinx.coroutines.**
