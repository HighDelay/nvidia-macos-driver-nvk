import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import org.lwjgl.PointerBuffer;
import org.lwjgl.system.MemoryStack;
import org.lwjgl.system.SharedLibrary;
import org.lwjgl.vulkan.VK;
import org.lwjgl.vulkan.VkApplicationInfo;
import org.lwjgl.vulkan.VkInstance;
import org.lwjgl.vulkan.VkInstanceCreateInfo;
import org.lwjgl.vulkan.VkPhysicalDevice;
import org.lwjgl.vulkan.VkPhysicalDeviceProperties;
import static org.lwjgl.sdl.SDLError.*;
import static org.lwjgl.sdl.SDLInit.*;
import static org.lwjgl.sdl.SDLVulkan.*;
import static org.lwjgl.vulkan.VK13.*;

/** Exercises vanilla 26.3's loader bootstrap with a JVM property and no mod.
 * Run on the first thread with Minecraft, its logging dependencies and LWJGL
 * 3.4.3 jars on the classpath. Does not start the game or change configuration.
 */
public final class ModFreeProbe {
    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }

    public static void main(String[] args) throws Exception {
        require(args.length == 1, "Expected the absolute frontend library path");
        // Invoke the unmodified game's method rather than substituting mod code.
        Class<?> bootstrap = Class.forName("com.mojang.blaze3d.platform.NativeLibrariesBootstrap");
        Method load = bootstrap.getDeclaredMethod("tryLoadingVulkan");
        load.setAccessible(true);
        require(Boolean.TRUE.equals(load.invoke(null)), "Vanilla Vulkan bootstrap failed");
        SharedLibrary provider = (SharedLibrary) VK.getFunctionProvider();
        System.out.println("PROVIDER " + provider.getPath());
        require(Files.isSameFile(Path.of(args[0]), Path.of(provider.getPath())),
                "Vanilla loaded a different provider");
        require(SDL_Init(SDL_INIT_VIDEO), SDL_GetError());
        try {
            require(SDL_Vulkan_LoadLibrary(provider.getPath()), SDL_GetError());
            try {
                require(provider.getFunctionAddress("vkGetInstanceProcAddr") ==
                        SDL_Vulkan_GetVkGetInstanceProcAddr(), "SDL/provider mismatch");
                try (MemoryStack stack = MemoryStack.stackPush()) {
                    VkApplicationInfo app = VkApplicationInfo.calloc(stack).sType$Default()
                            .apiVersion(VK_API_VERSION_1_3);
                    VkInstanceCreateInfo info = VkInstanceCreateInfo.calloc(stack).sType$Default()
                            .pApplicationInfo(app)
                            .ppEnabledExtensionNames(SDL_Vulkan_GetInstanceExtensions());
                    PointerBuffer handle = stack.mallocPointer(1);
                    require(vkCreateInstance(info, null, handle) == VK_SUCCESS, "vkCreateInstance");
                    VkInstance instance = new VkInstance(handle.get(0), info);
                    try {
                        var count = stack.mallocInt(1);
                        require(vkEnumeratePhysicalDevices(instance, count, null) == VK_SUCCESS &&
                                count.get(0) > 0, "No Vulkan physical device");
                        PointerBuffer devices = stack.mallocPointer(count.get(0));
                        require(vkEnumeratePhysicalDevices(instance, count, devices) == VK_SUCCESS,
                                "vkEnumeratePhysicalDevices");
                        VkPhysicalDevice physical = new VkPhysicalDevice(devices.get(0), instance);
                        VkPhysicalDeviceProperties properties = VkPhysicalDeviceProperties.calloc(stack);
                        vkGetPhysicalDeviceProperties(physical, properties);
                        System.out.println("DEVICE " + properties.deviceNameString());
                        require(properties.vendorID() == 0x10de &&
                                properties.deviceNameString().contains("NVK"), "Expected NVIDIA NVK");
                    } finally {
                        vkDestroyInstance(instance, null);
                    }
                }
            } finally {
                SDL_Vulkan_UnloadLibrary();
            }
        } finally {
            SDL_Quit();
            VK.destroy();
        }
        System.out.println("PASS unmodified Minecraft bootstrap, JVM selection and SDL alignment");
    }
}
