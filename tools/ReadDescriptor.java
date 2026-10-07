/**
 * Prints what a built plugin jar declares about itself, read the way the client reads it.
 *
 * The point is to check a manifest against the jar it describes rather than trusting it. The
 * class is loaded WITHOUT being initialised - Class.forName(name, false, loader) - so no plugin
 * code runs here: a static initialiser is already somebody else's code, and this tool exists to
 * inspect code nobody has run yet.
 *
 *   java -cp client.jar:ReadDescriptor.dir ReadDescriptor plugin.jar some.plugin.Class
 *
 * Prints "key=value" lines, or "error=..." and exits 1.
 */
import java.io.File;
import java.lang.annotation.Annotation;
import java.lang.reflect.Method;
import java.net.URL;
import java.net.URLClassLoader;

public final class ReadDescriptor {

	public static void main(String[] args) {
		if (args.length < 2) {
			System.out.println("error=usage: ReadDescriptor <plugin.jar> <plugin class>");
			System.exit(1);
		}
		try {
			URL url = new File(args[0]).toURI().toURL();
			URLClassLoader loader = new URLClassLoader(new URL[] { url },
				ReadDescriptor.class.getClassLoader());
			try {
				Class<?> type = Class.forName(args[1], false, loader);

				// A plugin that does not extend Plugin is not a plugin, whatever it is annotated
				// with - the client's loader would walk past it and the jar would install as a
				// file that does nothing.
				Class<?> plugin = Class.forName("jagex2.client.plugin.Plugin", false, loader);
				System.out.println("isPlugin=" + plugin.isAssignableFrom(type));

				Class<?> annotation = Class.forName("jagex2.client.plugin.PluginDescriptor",
					false, loader);
				Annotation descriptor = type.getAnnotation((Class) annotation);
				if (descriptor == null) {
					System.out.println("hasDescriptor=false");
					System.out.println("apiLevel=0");
					return;
				}
				System.out.println("hasDescriptor=true");
				System.out.println("name=" + read(descriptor, "name", ""));
				System.out.println("key=" + read(descriptor, "key", ""));
				System.out.println("apiLevel=" + read(descriptor, "apiLevel", "0"));
			} finally {
				loader.close();
			}
		} catch (Throwable error) {
			System.out.println("error=" + error);
			System.exit(1);
		}
	}

	/**
	 * One element of the annotation, by name. Reflective because this tool is compiled against
	 * no particular client: an older client whose PluginDescriptor has no apiLevel() must read as
	 * the fallback rather than failing to compile or throwing.
	 */
	static String read(Annotation descriptor, String element, String fallback) {
		try {
			Method method = descriptor.annotationType().getMethod(element);
			Object value = method.invoke(descriptor);
			return value == null ? fallback : String.valueOf(value);
		} catch (Throwable missing) {
			return fallback;
		}
	}
}
